"""Run: python -m unittest discover -s tests (requires PyYAML and kubectl).

The test that runs player-stats.sh also needs sh and jq, and is skipped without them.
"""
import gzip
import importlib.util
import json
import os
import pathlib
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAMESPACE = "dripcraft-public"
PRIVATE_RANGES = {"10.0.0.0/8", "100.64.0.0/10", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"}


class DripcraftPublicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "manifests")], text=True
        )
        cls.objects = {(obj["kind"], obj["metadata"]["name"]): obj for obj in yaml.safe_load_all(text)}
        cls.server = cls.objects[("StatefulSet", "minecraft")]["spec"]["template"]["spec"]
        cls.creative = cls.objects[("StatefulSet", "minecraft-creative")]["spec"]["template"]["spec"]
        cls.creative_patches = json.loads((ROOT / "manifests" / "creative-tuning-patches.json").read_text())["patches"]
        cls.agent = cls.objects[("Deployment", "playit-agent")]["spec"]["template"]["spec"]
        cls.uptime = cls.objects[("CronJob", "uptime-check")]["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        cls.web_map = cls.objects[("Deployment", "web-map")]["spec"]["template"]["spec"]
        cls.archive = cls.objects[("Deployment", "archive")]["spec"]["template"]["spec"]
        cls.patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        cls.caddyfile = (ROOT / "manifests" / "Caddyfile").read_text()

    def container(self, name):
        return next(c for c in self.server["containers"] if c["name"] == name)

    def env(self, container="minecraft"):
        return {e["name"]: e for e in self.container(container)["env"]}

    def egress(self, policy):
        return self.objects[("NetworkPolicy", policy)]["spec"]["egress"]

    def objects_with_prefix(self, kind, prefix):
        """Find a generated object, whose name ends in a content hash."""
        return next(obj for (k, name), obj in self.objects.items() if k == kind and name.startswith(f"{prefix}-"))

    def test_namespace_enforces_restricted(self):
        labels = self.objects[("Namespace", NAMESPACE)]["metadata"]["labels"]
        self.assertEqual(labels["pod-security.kubernetes.io/enforce"], "restricted")

    def test_pods_are_locked_down(self):
        for pod in (self.server, self.creative, self.agent, self.uptime, self.web_map, self.archive):
            self.assertIs(pod["automountServiceAccountToken"], False)
            self.assertIs(pod["securityContext"]["runAsNonRoot"], True)
            self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
            for container in pod["containers"]:
                context = container["securityContext"]
                self.assertIs(context["allowPrivilegeEscalation"], False)
                self.assertIs(context["readOnlyRootFilesystem"], True)
                self.assertEqual(context["capabilities"]["drop"], ["ALL"])
                self.assertNotIn("add", context["capabilities"])

    def test_images_are_pinned_by_digest(self):
        for pod in (self.server, self.creative, self.agent, self.uptime, self.web_map, self.archive):
            for container in pod["containers"]:
                self.assertRegex(container["image"], r":[\w.-]+@sha256:[0-9a-f]{64}$")

    def test_only_whitelisted_online_accounts_can_join(self):
        env = self.env()
        for name in ("ONLINE_MODE", "ENABLE_WHITELIST", "ENFORCE_WHITELIST"):
            self.assertEqual(env[name]["value"], "TRUE", name)
        self.assertEqual(env["ENABLE_QUERY"]["value"], "FALSE")

    def test_bedrock_players_can_chat(self):
        self.assertEqual(self.env()["ENFORCE_SECURE_PROFILE"]["value"], "FALSE")

    def test_backups_pause_pre_generation_and_resume_only_what_they_paused(self):
        env = self.env("backup")
        self.assertIn("chunky pause", env["PRE_SAVE_ALL_SCRIPT"]["value"])
        self.assertIn("chunky continue", env["POST_BACKUP_SCRIPT"]["value"])
        self.assertIn("/tmp/chunky-paused", env["POST_BACKUP_SCRIPT"]["value"])

    def test_chunk_workers_are_set(self):
        config = yaml.safe_load((ROOT / "manifests" / "paper-global.yml").read_text())
        self.assertEqual(config["chunk-system"]["worker-threads"], 4)

    def test_tree_felling_wears_axes_fully_and_does_not_replant(self):
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        patch = next(p for p in patches if p["file"] == "/data/plugins/Timberella/config.yml")
        ops = {op["$set"]["path"]: op["$set"]["value"] for op in patch["ops"]}
        self.assertEqual(ops, {"$['enable_replant']": False, "$['tools']['durability_multiplier']": 1})

    def test_discord_cannot_run_server_commands_or_change_the_channel(self):
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        patch = next(p for p in patches if p["file"] == "/data/plugins/DiscordSRV/config.yml")
        ops = {op["$set"]["path"]: op["$set"]["value"] for op in patch["ops"]}
        self.assertEqual(ops, {
            "$['DiscordChatChannelConsoleCommandEnabled']": False,
            "$['ServerWatchdogEnabled']": False,
            "$['ChannelTopicUpdaterChannelTopicsAtShutdownEnabled']": False,
            "$['DiscordCannedResponses']": {},
            "$['MaximumAttemptsForSystemDNSBeforeUsingFallbackDNS']": -1,
        })
        patch = next(p for p in patches if p["file"] == "/data/plugins/DiscordSRV/messages.yml")
        self.assertEqual({op["$set"]["value"] for op in patch["ops"]}, {""})

    def test_discord_keeps_using_the_cluster_dns(self):
        # After three failed lookups in a row DiscordSRV asks public DNS servers instead and
        # never goes back. The servers may only reach the cluster's DNS, so a short internet
        # outage would leave the bot offline until the next restart. -1 turns the switch off.
        for patches in (self.patches, self.creative_patches):
            patch = next(p for p in patches if p["file"] == "/data/plugins/DiscordSRV/config.yml")
            op = next(op["$set"] for op in patch["ops"] if "SystemDNS" in op["$set"]["path"])
            self.assertEqual((op["value"], op["value-type"]), (-1, "int"))
        for policy in ("minecraft-egress", "minecraft-creative-egress"):
            dns = [r for r in self.egress(policy) if any(p["port"] == 53 for p in r["ports"])]
            self.assertEqual(len(dns), 1, policy)
            self.assertEqual([p["podSelector"]["matchLabels"] for p in dns[0]["to"]], [{"k8s-app": "kube-dns"}], policy)

    def test_discord_token_comes_from_an_optional_secret(self):
        ref = self.env()["DISCORDSRV_TOKEN"]["valueFrom"]["secretKeyRef"]
        self.assertEqual((ref["name"], ref["key"]), ("discord-bot", "token"))
        self.assertIs(ref["optional"], True)

    def test_no_patch_targets_the_copied_paper_config(self):
        # The image replaces this file with the short copy before it patches, so a patch
        # finds nothing to set and the server fails to start.
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        self.assertNotIn("/data/config/paper-global.yml", [patch["file"] for patch in patches])

    def test_floodgate_is_pinned_to_one_build(self):
        self.assertRegex(self.env()["PLUGINS"]["value"], r"/projects/floodgate/versions/[\d.]+/builds/\d+/downloads/spigot$")

    def test_secrets_are_referenced_not_inlined(self):
        for container in ("minecraft", "backup", "discord-status"):
            self.assertIn("secretKeyRef", self.env(container)["RCON_PASSWORD"]["valueFrom"])
        self.assertEqual(
            next(v for v in self.agent["volumes"] if v["name"] == "secret")["secret"]["secretName"],
            "playit-agent",
        )

    def test_heap_fits_in_the_pod_limit(self):
        server = next(c for c in self.server["containers"] if c["name"] == "minecraft")
        heap = int(self.env()["MEMORY"]["value"].rstrip("G"))
        limit = int(server["resources"]["limits"]["memory"].rstrip("Gi"))
        self.assertGreaterEqual(limit - heap, 4)
        self.assertEqual(server["resources"]["requests"]["memory"], server["resources"]["limits"]["memory"])
        self.assertNotIn("cpu", server["resources"]["limits"])

    def test_tuning_patches_are_mounted_where_the_image_reads_them(self):
        server = next(c for c in self.server["containers"] if c["name"] == "minecraft")
        mounts = {m["mountPath"]: m for m in server["volumeMounts"]}
        path = self.env()["PATCH_DEFINITIONS"]["value"]
        self.assertEqual(mounts[path]["subPath"], pathlib.PurePosixPath(path).name)
        self.assertIn(mounts[path]["subPath"], self.objects_with_prefix("ConfigMap", "paper-config")["data"])

    def test_spawn_range_matches_simulation_distance(self):
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        spawn_range = next(
            op["$set"]["value"]
            for patch in patches
            for op in patch["ops"]
            if "mob-spawn-range" in op["$set"]["path"]
        )
        self.assertEqual(spawn_range, int(self.env()["SIMULATION_DISTANCE"]["value"]))

    def test_proxy_protocol_is_on_for_both_paper_and_health_checks(self):
        paper = yaml.safe_load((ROOT / "manifests" / "paper-global.yml").read_text())
        self.assertIs(paper["proxies"]["proxy-protocol"], True)
        self.assertEqual(self.env()["USES_PROXY_PROTOCOL"]["value"], "TRUE")

    def test_tunnel_origin_is_a_fixed_address(self):
        service = self.objects[("Service", "minecraft")]["spec"]
        self.assertEqual(service["clusterIP"], "10.96.25.65")
        self.assertEqual(service.get("type", "ClusterIP"), "ClusterIP")
        self.assertEqual(
            [(p["protocol"], p["port"]) for p in service["ports"]],
            [("TCP", 25565), ("UDP", 24454), ("UDP", 19132), ("TCP", 8080)],
        )

    def test_web_map_tunnel_origin_is_a_fixed_address(self):
        service = self.objects[("Service", "web-map")]["spec"]
        self.assertEqual(service["clusterIP"], "10.96.25.66")
        self.assertEqual(service.get("type", "ClusterIP"), "ClusterIP")
        self.assertEqual(
            [(p["protocol"], p["port"]) for p in service["ports"]],
            [("TCP", 8080), ("TCP", 8443)],
        )

    def test_caddy_listens_where_the_tunnel_and_service_send_traffic(self):
        ports = {p["name"]: p["containerPort"] for p in self.web_map["containers"][0]["ports"]}
        self.assertIn(f"http_port {ports['http']}", self.caddyfile)
        self.assertIn(f"https_port {ports['https']}", self.caddyfile)

    def test_caddy_proxies_to_the_servers_map_port(self):
        server = {p["name"]: p["containerPort"] for p in self.container("minecraft")["ports"]}
        # The map is under /map/, and the page at the root links to it there.
        self.assertIn(f"handle_path /map/* {{\n\t\treverse_proxy minecraft:{server['map']}\n\t}}", self.caddyfile)
        self.assertIn('<a href="/map/">', (ROOT / "manifests" / "hub.html").read_text(encoding="utf-8"))
        service = self.objects[("Service", "minecraft")]["spec"]["ports"]
        self.assertIn({"name": "map", "port": server["map"], "targetPort": "map", "protocol": "TCP"}, service)

    def test_map_domain_stays_out_of_git(self):
        env = {e["name"]: e for e in self.web_map["containers"][0]["env"]}
        ref = env["MAP_DOMAIN"]["valueFrom"]["secretKeyRef"]
        self.assertEqual((ref["name"], ref["key"]), ("web-map", "domain"))
        self.assertIs(ref["optional"], True)
        self.assertIn("{$MAP_DOMAIN:localhost} {", self.caddyfile)

    def test_map_certificates_survive_restarts(self):
        # Let's Encrypt limits new certificates for the same name to a few a week.
        caddy = self.web_map["containers"][0]
        mount = next(m for m in caddy["volumeMounts"] if m["mountPath"] == "/data")
        volume = next(v for v in self.web_map["volumes"] if v["name"] == mount["name"])
        claim = self.objects[("PersistentVolumeClaim", volume["persistentVolumeClaim"]["claimName"])]
        self.assertIn("Prune=false", claim["metadata"]["annotations"]["argocd.argoproj.io/sync-options"])
        deployment = self.objects[("Deployment", "web-map")]["spec"]
        self.assertEqual(deployment["strategy"]["type"], "Recreate")

    def test_caddyfile_is_mounted_where_caddy_reads_it(self):
        caddy = self.web_map["containers"][0]
        mount = next(m for m in caddy["volumeMounts"] if m["mountPath"] == "/etc/caddy")
        volume = next(v for v in self.web_map["volumes"] if v["name"] == mount["name"])
        self.assertIn("Caddyfile", self.objects[("ConfigMap", volume["configMap"]["name"])]["data"])

    def test_map_renders_with_one_thread(self):
        patch = next(p for p in self.patches if p["file"] == "/data/plugins/squaremap/config.yml")
        ops = {op["$set"]["path"]: op["$set"]["value"] for op in patch["ops"]}
        default = "$['world-settings']['default']['map']"
        for mode in ("full", "radius", "background"):
            self.assertEqual(ops[f"{default}['render']['{mode}']['workers']"], 1, mode)
        self.assertEqual(ops[f"{default}['image-saving']['workers']"], 1)

    def test_map_tiles_stay_out_of_backups(self):
        excludes = self.env("backup")["EXCLUDES"]["value"].split(",")
        self.assertIn("plugins/squaremap/web/tiles", excludes)

    # The player statistics page.

    def web_map_container(self, name):
        return next(c for c in self.web_map["containers"] if c["name"] == name)

    def test_caddy_is_still_the_proxys_first_container(self):
        # The tests above read Caddy as the first container.
        self.assertEqual([c["name"] for c in self.web_map["containers"]], ["caddy", "player-stats"])

    def test_stats_are_read_from_the_survival_servers_folder_and_world(self):
        stats = self.web_map_container("player-stats")
        env = {e["name"]: e["value"] for e in stats["env"]}
        self.assertEqual(env["WORLD"], self.env()["LEVEL"]["value"])
        mount = next(m for m in stats["volumeMounts"] if m["name"] == "server")
        server = next(m for m in self.container("minecraft")["volumeMounts"] if m["name"] == "data")
        self.assertEqual(mount["subPath"], server["subPath"])
        script = (ROOT / "manifests" / "player-stats.sh").read_text()
        self.assertIn(f"server=${{SERVER_DIR:-{mount['mountPath']}}}", script)
        # A StatefulSet names a pod's claim after the template, its own name and the pod's number.
        statefulset = self.objects[("StatefulSet", "minecraft")]
        template = statefulset["spec"]["volumeClaimTemplates"][0]["metadata"]["name"]
        volume = next(v for v in self.web_map["volumes"] if v["name"] == "server")
        self.assertEqual(volume["persistentVolumeClaim"]["claimName"], f"{template}-{statefulset['metadata']['name']}-0")

    def test_stats_cannot_change_the_world(self):
        volume = next(v for v in self.web_map["volumes"] if v["name"] == "server")
        self.assertIs(volume["persistentVolumeClaim"]["readOnly"], True)
        mount = next(m for m in self.web_map_container("player-stats")["volumeMounts"] if m["name"] == "server")
        self.assertIs(mount["readOnly"], True)

    def test_caddy_never_sees_the_servers_folder(self):
        # It holds the RCON password and the whitelist, and Caddy faces the web.
        self.assertNotIn("server", [m["name"] for m in self.web_map_container("caddy")["volumeMounts"]])

    def test_caddy_serves_the_stats_page_and_numbers_from_where_they_are_mounted(self):
        mounts = {m["name"]: m for m in self.web_map_container("caddy")["volumeMounts"]}
        for volume in ("stats-page", "stats-data"):
            self.assertIn(f"root * {mounts[volume]['mountPath']}\n", self.caddyfile)
            self.assertIs(mounts[volume]["readOnly"], True)
        page = next(v for v in self.web_map["volumes"] if v["name"] == "stats-page")["configMap"]
        self.assertEqual(
            [item["key"] for item in page["items"]],
            ["index.html", "hub.css", "hub.js", "fraunces.woff2", "figtree.woff2"],
        )
        files = self.objects_with_prefix("ConfigMap", "player-stats")
        for item in page["items"]:
            self.assertEqual(item["path"], item["key"])
            # The fonts are not text, so they are kept apart from the rest.
            self.assertIn(item["key"], files["binaryData" if item["key"].endswith(".woff2") else "data"])
        self.assertIn("index.html", self.objects_with_prefix("ConfigMap", "player-stats")["data"])
        # The hub asks for /data/stats.json.
        self.assertIn("handle_path /data/* {", self.caddyfile)
        self.assertIn('fetch("/data/stats.json"', (ROOT / "manifests" / "hub.js").read_text(encoding="utf-8"))

    def test_stats_are_written_where_caddy_reads_them(self):
        script = (ROOT / "manifests" / "player-stats.sh").read_text()
        written = next(m for m in self.web_map_container("player-stats")["volumeMounts"] if m["name"] == "stats-data")
        self.assertIn(f"out=${{OUT_FILE:-{written['mountPath']}/stats.json}}", script)

    def test_stats_script_is_mounted_where_it_is_run(self):
        container = self.web_map_container("player-stats")
        script = pathlib.PurePosixPath(container["command"][-1])
        mount = next(m for m in container["volumeMounts"] if m["mountPath"] == str(script.parent))
        volume = next(v for v in self.web_map["volumes"] if v["name"] == mount["name"])
        self.assertIn({"key": script.name, "path": script.name}, volume["configMap"]["items"])
        self.assertIn(script.name, self.objects[("ConfigMap", volume["configMap"]["name"])]["data"])

    def test_stats_use_an_image_the_node_already_has_and_almost_no_cpu(self):
        container = self.web_map_container("player-stats")
        self.assertEqual(container["image"], self.container("minecraft")["image"])
        self.assertLessEqual(int(container["resources"]["requests"]["cpu"].rstrip("m")), 5)

    def test_hub_loads_nothing_from_other_sites(self):
        # Skins and flags are copies the script fetched. A link the reader may follow is fine.
        page = (ROOT / "manifests" / "hub.html").read_text(encoding="utf-8")
        loaded = re.findall(r"<(?:link|script|img|iframe|source)\b[^>]*>", page)
        addresses = [re.search(r"(?:src|href)=\"([^\"]*)\"", tag).group(1) for tag in loaded]
        # The stylesheet, the script and the two fonts are files beside the page.
        self.assertEqual(
            sorted(a for a in addresses if not a.startswith("data:")),
            ["/figtree.woff2", "/fraunces.woff2", "/hub.css", "/hub.js"],
        )
        # The icon is a drawing written out in the page itself, which asks for nothing.
        icons = [a for a in addresses if a.startswith("data:")]
        self.assertEqual(len(icons), 1)
        self.assertTrue(icons[0].startswith("data:image/svg+xml,"))
        self.assertNotRegex(icons[0], r"(?i)href|url\(|image |script")
        # The only files the stylesheet asks for are the two fonts beside it.
        style = (ROOT / "manifests" / "hub.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", style)
        self.assertEqual(re.findall(r"url\(([^)]*)\)", style), ['"/fraunces.woff2"', '"/figtree.woff2"'])
        # The one address in the script is the name of the SVG format, which nothing fetches.
        script = (ROOT / "manifests" / "hub.js").read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")
        self.assertNotRegex(script, r"https?://|[\"'`]//")

    def test_every_page_of_the_hub_can_be_opened_by_its_address(self):
        # The hub draws its own pages, so an address like /members is not a file. Caddy
        # answers those with the hub, and the hub asks for its files from the root, so
        # they are found from a page however deep its address is.
        self.assertIn("try_files {path} /index.html", self.caddyfile)
        page = (ROOT / "manifests" / "hub.html").read_text(encoding="utf-8")
        script = (ROOT / "manifests" / "hub.js").read_text(encoding="utf-8")
        for address in re.findall(r"(?:href|src)=\"([^\"]*)\"", page):
            # The icon is written out in the page, so it is not an address.
            self.assertRegex(address, r"^(?:/|data:image/svg\+xml,|https://db-ip\.com$)", address)
        for address in re.findall(r"\"(/data/[^\"]*)\"", script):
            self.assertTrue(address.startswith("/data/"))
        self.assertNotRegex(script, r"[\"'`]data/")

    def test_caddy_never_sees_players_addresses(self):
        # The script keeps addresses and the country database in its state folder.
        script = (ROOT / "manifests" / "player-stats.sh").read_text()
        state = next(m for m in self.web_map_container("player-stats")["volumeMounts"] if m["name"] == "stats-state")
        self.assertIn(f"state=${{STATE_DIR:-{state['mountPath']}}}", script)
        self.assertNotIn("stats-state", [m["name"] for m in self.web_map_container("caddy")["volumeMounts"]])

    @unittest.skipUnless(shutil.which("sh") and shutil.which("jq"), "needs sh and jq")
    def test_stats_script_gathers_every_players_file(self):
        with tempfile.TemporaryDirectory() as folder:
            server = pathlib.Path(folder)
            stats = server / "dripcraft" / "players" / "stats"
            stats.mkdir(parents=True)
            (server / "whitelist.json").write_text(json.dumps([
                {"uuid": "aaaa", "name": "OldName"}, {"uuid": "bbbb", "name": ".Bedrock"},
            ]))
            (server / "usercache.json").write_text(json.dumps([{"uuid": "aaaa", "name": "NewName", "expiresOn": "x"}]))
            (stats / "aaaa.json").write_text(json.dumps({"stats": {
                "minecraft:custom": {"minecraft:play_time": 1200},
                "minecraft:mined": {"minecraft:stone": 3},
                "minecraft:picked_up": {"minecraft:stone": 3},
            }, "DataVersion": 1}))
            (stats / "bbbb.json").write_text(json.dumps({"stats": {}}))
            (stats / "cccc.json").write_text(json.dumps({"stats": {"minecraft:killed_by": {"minecraft:zombie": 1}}}))
            # The log: NewName joined from two addresses, and someone typed a line in
            # chat that looks like .Bedrock joining from a third.
            (server / "logs").mkdir()
            with gzip.open(server / "logs" / "2026-11-01-1.log.gz", "wt") as log:
                log.write("[10:00:00] [Server thread/INFO]: NewName[/5.5.5.5:1000] logged in with entity id 1 at (0, 0, 0)\n")
            (server / "logs" / "latest.log").write_text(
                "[11:00:00] [Server thread/INFO]: NewName[/1.2.3.4:1000] logged in with entity id 2 at (0, 0, 0)\n"
                "[11:01:00] [Async Chat Thread - #1/INFO]: <NewName> [11:00:00] [Server thread/INFO]: "
                ".Bedrock[/5.5.5.5:1000] logged in with entity id 3 at (0, 0, 0)\n"
                "[11:02:00] [Server thread/INFO]: .Bedrock[/9.9.9.9:1000] logged in with entity id 4 at (0, 0, 0)\n"
            )
            # The country database, the skin and its model are there already, as after an
            # earlier round, and FETCH=0 keeps the script off the internet.
            state = server / "state"
            state.mkdir()
            with gzip.open(state / "dbip.csv.gz", "wt") as database:
                database.write("0.0.0.0,0.255.255.255,ZZ\n1.2.3.0,1.2.3.255,DK\n5.5.5.0,5.5.5.255,SE\n::,1::,US\n")
            public = server / "public"
            (public / "skins").mkdir(parents=True)
            (public / "skins" / "aaaa.png").write_bytes(b"\x89PNG")
            (state / "skin-aaaa").write_text("slim\n")
            out = public / "stats.json"

            def run():
                return subprocess.run(
                    ["sh", (ROOT / "manifests" / "player-stats.sh").as_posix()],
                    env={
                        **os.environ, "ONCE": "1", "FETCH": "0", "SERVER_DIR": server.as_posix(),
                        "OUT_FILE": out.as_posix(), "STATE_DIR": state.as_posix(),
                    },
                    capture_output=True, text=True, timeout=60,
                )

            self.assertEqual(run().returncode, 0)
            data = json.loads(out.read_text())
            players = {p["uuid"]: p for p in data["players"]}
            # The country is that of the last login. An address the database does not
            # know gives none, and no address reaches the page.
            self.assertEqual([players[u]["country"] for u in ("aaaa", "bbbb", "cccc")], ["DK", None, None])
            self.assertEqual([players[u]["skin"] for u in ("aaaa", "bbbb", "cccc")], ["slim", None, None])
            for file in public.rglob("*"):
                if file.is_file():
                    self.assertNotRegex(file.read_text(errors="replace"), r"\d+\.\d+\.\d+\.\d+", file.name)
            # The name a player last joined with wins over the whitelist's.
            self.assertEqual([players[u]["name"] for u in ("aaaa", "bbbb", "cccc")], ["NewName", ".Bedrock", None])
            self.assertEqual(players["aaaa"]["stats"]["custom"], {"play_time": 1200})
            self.assertEqual(players["aaaa"]["stats"]["mined"], {"stone": 3})
            self.assertEqual(players["cccc"]["stats"]["killed_by"], {"zombie": 1})
            self.assertEqual(
                set(players["bbbb"]["stats"]), {"custom", "mined", "crafted", "used", "broken", "killed", "killed_by"}
            )
            self.assertIsInstance(players["aaaa"]["seen"], int)

            # A file the game is halfway through writing leaves the last good copy alone,
            # and the script still ends well, because a stopped container takes the map down.
            (stats / "aaaa.json").write_text('{"stats": {"minecraft:custom"')
            result = run()
            self.assertEqual(result.returncode, 0)
            self.assertIn("keeps its last numbers", result.stdout)
            self.assertEqual(json.loads(out.read_text()), data)

    def archive_container(self):
        (container,) = self.archive["containers"]
        return container

    def test_archive_has_a_pod_of_its_own(self):
        # A fault in it must not take the hub and the map off the tunnel, and a change
        # to the hub must not stop it noting where everyone is.
        self.assertNotIn("archive", [c["name"] for c in self.web_map["containers"]])
        deployment = self.objects[("Deployment", "archive")]
        self.assertEqual(deployment["spec"]["replicas"], 1)
        # Its volume can be mounted by one pod at a time.
        self.assertEqual(deployment["spec"]["strategy"], {"type": "Recreate"})

    def test_archive_cannot_change_the_world_or_the_grief_log(self):
        volume = next(v for v in self.archive["volumes"] if v["name"] == "server")
        self.assertIs(volume["persistentVolumeClaim"]["readOnly"], True)
        mount = next(m for m in self.archive_container()["volumeMounts"] if m["name"] == "server")
        self.assertIs(mount["readOnly"], True)
        # Prism's database is opened for reading only as well. So is the archive by the
        # feed. The one connection that can write is the gathering's own, to the archive.
        script = (ROOT / "manifests" / "archive.py").read_text(encoding="utf-8")
        self.assertEqual(script.count('"?mode=ro"'), 2)
        self.assertEqual(len(re.findall(r"sqlite3\.connect\(", script)), 4)
        self.assertEqual(len(re.findall(r"sqlite3\.connect\(address", script)), 3)

    def test_archive_reads_the_survival_servers_folder_and_world(self):
        container = self.archive_container()
        env = {e["name"]: e["value"] for e in container["env"]}
        self.assertEqual(env["WORLD"], self.env()["LEVEL"]["value"])
        mount = next(m for m in container["volumeMounts"] if m["name"] == "server")
        server = next(m for m in self.container("minecraft")["volumeMounts"] if m["name"] == "data")
        self.assertEqual(mount["subPath"], server["subPath"])
        script = (ROOT / "manifests" / "archive.py").read_text(encoding="utf-8")
        self.assertIn(f'SERVER = os.environ.get("SERVER_DIR", "{mount["mountPath"]}")', script)
        statefulset = self.objects[("StatefulSet", "minecraft")]
        template = statefulset["spec"]["volumeClaimTemplates"][0]["metadata"]["name"]
        volume = next(v for v in self.archive["volumes"] if v["name"] == "server")
        self.assertEqual(volume["persistentVolumeClaim"]["claimName"], f"{template}-{statefulset['metadata']['name']}-0")

    def test_archive_is_kept_on_a_volume_that_is_never_removed(self):
        # What is in it cannot be gathered again.
        claim = self.objects[("PersistentVolumeClaim", "archive")]
        self.assertEqual(claim["metadata"]["annotations"]["argocd.argoproj.io/sync-options"], "Prune=false,Delete=false")
        self.assertEqual(claim["spec"]["storageClassName"], "local-path-retain")
        mount = next(m for m in self.archive_container()["volumeMounts"] if m["name"] == "archive")
        volume = next(v for v in self.archive["volumes"] if v["name"] == "archive")
        self.assertEqual(volume["persistentVolumeClaim"]["claimName"], "archive")
        self.assertNotIn("readOnly", mount)
        script = (ROOT / "manifests" / "archive.py").read_text(encoding="utf-8")
        self.assertIn(f'ARCHIVE = os.environ.get("ARCHIVE_FILE", "{mount["mountPath"]}/archive.db")', script)

    def test_archive_script_is_mounted_where_it_is_run(self):
        container = self.archive_container()
        script = pathlib.PurePosixPath(container["command"][-1])
        mount = next(m for m in container["volumeMounts"] if m["mountPath"] == str(script.parent))
        volume = next(v for v in self.archive["volumes"] if v["name"] == mount["name"])
        self.assertIn(script.name, self.objects[("ConfigMap", volume["configMap"]["name"])]["data"])
        # Unbuffered, so what it logs shows at once.
        self.assertEqual(container["command"][:2], ["python3", "-u"])

    def test_archive_reserves_almost_no_cpu(self):
        self.assertLessEqual(int(self.archive_container()["resources"]["requests"]["cpu"].rstrip("m")), 5)

    def test_a_question_to_the_archive_sorts_on_disk(self):
        # The pod's own files cannot be written, so without a folder for temporary files
        # SQLite cannot sort or group, or does it in memory, which once stopped the pod.
        mount = next(m for m in self.archive_container()["volumeMounts"] if m["name"] == "tmp")
        self.assertEqual(mount["mountPath"], "/tmp")
        volume = next(v for v in self.archive["volumes"] if v["name"] == "tmp")
        self.assertEqual(set(volume), {"name", "emptyDir"})
        self.assertNotIn("medium", volume["emptyDir"])
        script = (ROOT / "manifests" / "archive.py").read_text(encoding="utf-8")
        self.assertNotIn("temp_store", script)

    def test_archive_reaches_only_the_map_and_only_the_ledger_reads_it(self):
        # It reaches the cluster's DNS and the survival server's map port, where it asks
        # who is on the map, and nothing else: no address on the internet or anywhere.
        rules = self.egress("archive-egress")
        self.assertEqual(len(rules), 2)
        dns, server = rules
        self.assertEqual(dns["to"][0]["podSelector"]["matchLabels"], {"k8s-app": "kube-dns"})
        self.assertEqual(server["to"], [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "minecraft"}}}])
        self.assertEqual(server["ports"], [{"protocol": "TCP", "port": 8080}])
        self.assertNotIn("ipBlock", json.dumps(rules))
        script = (ROOT / "manifests" / "archive.py").read_text(encoding="utf-8")
        self.assertIn('PLAYERS_URL = os.environ.get("PLAYERS_URL", "http://minecraft:8080/tiles/players.json")', script)
        self.assertEqual(re.findall(r"https?://[^\"' )]+", script), ["http://minecraft:8080/tiles/players.json"])

        # One policy lets anything connect to it, and it lets in one source: pods with
        # the ledger's name, in the ledger's namespace, on the feed's port.
        labels = self.objects[("Deployment", "archive")]["spec"]["template"]["metadata"]["labels"]
        letting_in = [
            name for (kind, name), policy in self.objects.items()
            if kind == "NetworkPolicy" and policy["spec"].get("ingress")
            and all(labels.get(k) == v for k, v in policy["spec"]["podSelector"].get("matchLabels", {}).items())
        ]
        self.assertEqual(letting_in, ["archive-ingress"])
        spec = self.objects[("NetworkPolicy", "archive-ingress")]["spec"]
        self.assertEqual(spec["podSelector"], {"matchLabels": {"app.kubernetes.io/name": "archive"}})
        self.assertEqual(spec["policyTypes"], ["Ingress"])
        (rule,) = spec["ingress"]
        # Both selectors are in one entry, so both must hold. As two entries, either would do.
        self.assertEqual(rule["from"], [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "dripcraft-ledger"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "dripcraft-ledger"}},
        }])
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 8080}])

        # The pod, its Service and the script agree on that port, and there is no other.
        container = self.archive_container()
        self.assertEqual(container["ports"], [{"name": "feed", "containerPort": 8080, "protocol": "TCP"}])
        self.assertIn('FEED_PORT = int(os.environ.get("FEED_PORT", "8080"))', script)
        self.assertNotIn("FEED_PORT", [e["name"] for e in container["env"]])
        service = self.objects[("Service", "archive")]["spec"]
        self.assertEqual(service["selector"], {"app.kubernetes.io/name": "archive"})
        self.assertEqual(service["ports"], [{"name": "feed", "port": 8080, "targetPort": "feed", "protocol": "TCP"}])
        # Reached by name from inside the cluster, and from nowhere else.
        self.assertEqual(service.get("type", "ClusterIP"), "ClusterIP")
        self.assertNotIn("clusterIP", service)

        # The feed gives out and takes nothing in.
        self.assertEqual(re.findall(r"def do_[A-Z]+", script), ["def do_GET"])
        # Nothing restarts the pod, and the gathering with it, because the feed does not answer.
        self.assertNotIn("livenessProbe", container)

    def test_archive_keeps_what_the_server_notes(self):
        script = (ROOT / "manifests" / "archive.py").as_posix()

        def prism_database(path, rows):
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE prism_actions (action_id INTEGER PRIMARY KEY, action varchar(25));
                CREATE TABLE prism_players (player_id INTEGER PRIMARY KEY, player varchar(32), player_uuid char(36));
                CREATE TABLE prism_activities (
                    activity_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp bigint, x INT, action_id bigint,
                    cause_player_id bigint, descriptor varchar(255), serialized_data clob);
                INSERT INTO prism_actions VALUES (1, 'block-break'), (2, 'block-place');
                INSERT INTO prism_players VALUES (1, 'Ada', 'aaaaaaaa-0000-0000-0000-000000000001');
            """)
            db.executemany(
                "INSERT INTO prism_activities (timestamp, x, action_id, cause_player_id, descriptor, serialized_data) VALUES (?, ?, ?, ?, ?, ?)", rows)
            db.commit()
            db.close()

        with tempfile.TemporaryDirectory() as folder:
            server = pathlib.Path(folder) / "server"
            stats = server / "dripcraft" / "players" / "stats"
            stats.mkdir(parents=True)
            prism = server / "plugins" / "prism" / "prism.db"
            prism.parent.mkdir(parents=True)
            listed = pathlib.Path(folder) / "players.json"
            archive = pathlib.Path(folder) / "kept" / "archive.db"

            def run(now=None, **more):
                env = {
                    **os.environ, "ONCE": "1", "SERVER_DIR": server.as_posix(), "ARCHIVE_FILE": archive.as_posix(),
                    "PLAYERS_URL": listed.as_uri(), "BATCH": "2", **more,
                }
                result = subprocess.run([sys.executable, script], env=env, capture_output=True, text=True, timeout=60)
                # It ends well whatever it finds, because it is restarted until it does.
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                return result.stdout

            def kept(query):
                db = sqlite3.connect(archive)
                try:
                    return db.execute(query).fetchall()
                finally:
                    db.close()

            # Nothing to read yet: no grief log, no statistics, and a map that does not answer.
            out = run()
            self.assertIn("Prism has no database yet", out)
            self.assertIn("list of players cannot be read", out)
            self.assertEqual(kept("SELECT count(*) FROM positions"), [(0,)])

            # Three rows in the grief log, a player on the map, and a statistics file.
            prism_database(prism, [(100, 5, 1, 1, "Stone", None), (101, 6, 2, 1, "Dirt", "{blob}"), (102, 7, 1, None, None, None)])
            before = prism.read_bytes()
            listed.write_text(json.dumps({"max": 10, "players": [
                {"name": "Ada", "uuid": "aaaaaaaa000000000000000000000001", "world": "minecraft_overworld",
                 "x": 12, "y": 64, "z": -7, "yaw": 90, "health": 20, "armor": 4},
                {"name": "broken"},
            ]}))
            (stats / "aaaaaaaa-0000-0000-0000-000000000001.json").write_text('{"stats": {"minecraft:custom": {"minecraft:jump": 3}}}')
            (stats / "bbbbbbbb-0000-0000-0000-000000000002.json").write_text('{"stats": {"minecraft:cust')
            self.assertIn("1 positions, 3 events, 1 snapshots", run())
            # Every column of Prism's comes across as it was, more than one batch of them.
            self.assertEqual(kept("SELECT source, activity_id, timestamp, x, action_id, cause_player_id, descriptor, serialized_data FROM prism_activities"), [
                (0, 1, 100, 5, 1, 1, "Stone", None), (0, 2, 101, 6, 2, 1, "Dirt", "{blob}"), (0, 3, 102, 7, 1, None, None, None),
            ])
            self.assertEqual(kept("SELECT source, action_id, action FROM prism_actions"), [(0, 1, "block-break"), (0, 2, "block-place")])
            self.assertEqual(kept("SELECT player, player_uuid FROM prism_players"), [("Ada", "aaaaaaaa-0000-0000-0000-000000000001")])
            # The map leaves the dashes out of a UUID. The archive has them, like the other sources.
            self.assertEqual(kept("SELECT uuid, name FROM players"), [("aaaaaaaa-0000-0000-0000-000000000001", "Ada")])
            self.assertEqual(kept("SELECT world, x, y, z, yaw, health, armor FROM positions"), [("minecraft_overworld", 12, 64, -7, 90, 20, 4)])
            ((uuid, text),) = kept("SELECT uuid, stats FROM totals")
            self.assertEqual(uuid, "aaaaaaaa-0000-0000-0000-000000000001")
            self.assertEqual(json.loads(zlib.decompress(text)), {"stats": {"minecraft:custom": {"minecraft:jump": 3}}})
            # The grief log itself is not touched.
            self.assertEqual(prism.read_bytes(), before)

            # Looking again keeps nothing twice, but notes where everyone is now.
            self.assertIn("1 positions, 0 events, 0 snapshots", run())
            self.assertEqual(kept("SELECT count(*) FROM prism_activities"), [(3,)])
            self.assertEqual(kept("SELECT count(*) FROM totals"), [(1,)])

            # Prism writes more, deletes its oldest rows, renames a player and gains a column.
            db = sqlite3.connect(prism)
            db.execute("ALTER TABLE prism_activities ADD COLUMN reversed boolean")
            db.execute("INSERT INTO prism_activities (timestamp, x, action_id, reversed) VALUES (103, 8, 2, 1)")
            db.execute("DELETE FROM prism_activities WHERE activity_id < 3")
            db.execute("UPDATE prism_players SET player = 'Ada_L'")
            db.execute("INSERT INTO prism_actions VALUES (3, 'item-trade')")
            db.commit()
            db.close()
            self.assertIn("1 events", run())
            self.assertEqual(kept("SELECT source, activity_id, x, reversed FROM prism_activities"), [(0, 1, 5, None), (0, 2, 6, None), (0, 3, 7, None), (0, 4, 8, 1)])
            self.assertEqual(kept("SELECT action FROM prism_actions ORDER BY action_id"), [("block-break",), ("block-place",), ("item-trade",)])
            # Names are all read again each hour, so a new name arrives then.
            self.assertEqual(kept("SELECT player FROM prism_players"), [("Ada",)])
            db = sqlite3.connect(archive)
            db.execute("UPDATE meta SET v = 0 WHERE k = 'prism_names_at'")
            db.commit()
            db.close()
            run()
            self.assertEqual(kept("SELECT player FROM prism_players"), [("Ada_L",)])

            # Prism deleting every row it has is not another database: its numbers carry on.
            db = sqlite3.connect(prism)
            db.execute("DELETE FROM prism_activities")
            db.commit()
            self.assertNotIn("source", run())
            db.execute("INSERT INTO prism_activities (timestamp, x, action_id) VALUES (104, 9, 1)")
            db.commit()
            db.close()
            self.assertIn("1 events", run())
            self.assertEqual(kept("SELECT source, activity_id FROM prism_activities ORDER BY 1, 2")[-1], (0, 5))

            # A grief log that is replaced starts its numbers again. Its rows are kept
            # apart from the old ones, which stay.
            prism.unlink()
            prism_database(prism, [(200, 1, 2, 1, "Glass", None)])
            out = run()
            self.assertIn("kept as source 1", out)
            self.assertIn("1 events", out)
            self.assertEqual(kept("SELECT source, count(*) FROM prism_activities GROUP BY source"), [(0, 5), (1, 1)])
            self.assertEqual(kept("SELECT source, action FROM prism_actions WHERE action_id = 1 ORDER BY source"), [(0, "block-break"), (1, "block-break")])
            self.assertEqual(kept("SELECT source, activity_id, descriptor FROM prism_activities WHERE source = 1"), [(1, 1, "Glass")])

            # The game saves the statistics again, and someone else joins the map.
            path = stats / "aaaaaaaa-0000-0000-0000-000000000001.json"
            path.write_text('{"stats": {"minecraft:custom": {"minecraft:jump": 9}}}')
            os.utime(path, (2_000_000_000, 2_000_000_000))
            listed.write_text(json.dumps({"players": [
                {"name": "Ada_L", "uuid": "aaaaaaaa000000000000000000000001", "world": "minecraft_the_nether", "x": 1.6, "y": 2, "z": 3},
                {"name": "Bo", "uuid": "bbbbbbbb000000000000000000000002", "world": "minecraft_overworld", "x": 4, "y": 5, "z": 6},
            ]}))
            self.assertIn("2 positions, 0 events, 1 snapshots", run())
            self.assertEqual(kept("SELECT uuid, t, length(stats) > 0 FROM totals ORDER BY t")[-1], ("aaaaaaaa-0000-0000-0000-000000000001", 2_000_000_000, 1))
            self.assertEqual(kept("SELECT name FROM players ORDER BY player"), [("Ada_L",), ("Bo",)])
            self.assertEqual(
                kept("SELECT name, world, x FROM positions JOIN players USING (player) WHERE t = (SELECT max(t) FROM positions) ORDER BY name"),
                [("Ada_L", "minecraft_the_nether", 2), ("Bo", "minecraft_overworld", 4)],
            )
            # Looks close together are one stretch of watching.
            ((start, end, samples),) = kept('SELECT start, "end", samples FROM watches')
            self.assertGreaterEqual(end, start)
            self.assertGreaterEqual(samples, 2)

            # An archive that cannot be opened is said, and tried again, without stopping.
            out = run(ARCHIVE_FILE=(stats / "aaaaaaaa-0000-0000-0000-000000000001.json" / "x.db").as_posix())
            self.assertIn("The archive cannot be used", out)

    # The feed.

    ADA = "aaaaaaaa-0000-0000-0000-000000000001"
    BO = "bbbbbbbb-0000-0000-0000-000000000002"
    # Prism's tables as its schema version 403 has them, which is what the server runs.
    PRISM_TABLES = """
        CREATE TABLE prism_actions (action_id INTEGER PRIMARY KEY, action varchar(25));
        CREATE TABLE prism_worlds (world_id INTEGER PRIMARY KEY, world varchar(255), world_uuid char(36));
        CREATE TABLE prism_blocks (block_id INTEGER PRIMARY KEY, ns varchar(55), name varchar(55), data varchar(255), translation_key varchar(155));
        CREATE TABLE prism_items (item_id INTEGER PRIMARY KEY, material varchar(45), data clob, airtag_id bigint);
        CREATE TABLE prism_entity_types (entity_type_id INTEGER PRIMARY KEY, entity_type varchar(45), translation_key varchar(155));
        CREATE TABLE prism_players (player_id INTEGER PRIMARY KEY, player varchar(32), player_uuid char(36));
        CREATE TABLE prism_causes (cause_id INTEGER PRIMARY KEY, cause varchar(155));
        CREATE TABLE prism_activities (
            activity_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp bigint, world_id bigint, x INT, y INT, z INT,
            action_id bigint, affected_item_id bigint, affected_item_quantity INT, affected_block_id bigint,
            replaced_block_id bigint, affected_entity_type_id bigint, affected_player_id bigint, cause_id bigint,
            cause_player_id bigint, cause_entity_type_id bigint, cause_block_id bigint, descriptor varchar(255),
            metadata varchar(255), serializer_version INT, serialized_data clob, reversed boolean);
    """

    def archive_script(self):
        """manifests/archive.py as a module, with nothing of it started."""
        spec = importlib.util.spec_from_file_location("archive_script", ROOT / "manifests" / "archive.py")
        module = importlib.util.module_from_spec(spec)
        writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = writes
        return module

    def prism_like_the_servers(self, path, names, rows):
        """A grief log with Prism's own tables, the given names for its numbers, and the given rows."""
        db = sqlite3.connect(path)
        db.executescript(self.PRISM_TABLES)
        for table, values in names.items():
            db.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(values[0]))})", values)
        for row in rows:
            db.execute(f"INSERT INTO prism_activities ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        db.commit()
        db.close()

    def gather_once(self, server, archive):
        """Runs the gathering for one look at each source, as the pod does over and over."""
        env = {
            **os.environ, "ONCE": "1", "SERVER_DIR": server.as_posix(), "ARCHIVE_FILE": archive.as_posix(),
            "PLAYERS_URL": (server / "nobody.json").as_uri(),
        }
        script = (ROOT / "manifests" / "archive.py").as_posix()
        result = subprocess.run([sys.executable, script], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_feed_gives_out_prisms_rows_in_order_with_their_names(self):
        module = self.archive_script()
        # The numbers the ledger is written against.
        self.assertEqual((module.FEED_ROWS, module.FEED_MOST), (1000, 5000))

        with tempfile.TemporaryDirectory() as folder:
            server = pathlib.Path(folder) / "server"
            prism = server / "plugins" / "prism" / "prism.db"
            prism.parent.mkdir(parents=True)
            archive = pathlib.Path(folder) / "kept" / "archive.db"
            module.ARCHIVE = archive.as_posix()

            listening = module.http.server.ThreadingHTTPServer(("127.0.0.1", 0), module.Feed)
            self.addCleanup(listening.server_close)
            self.addCleanup(listening.shutdown)
            threading.Thread(target=listening.serve_forever, daemon=True).start()
            address = f"http://127.0.0.1:{listening.server_address[1]}"
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def ask(question="", path="/feed/activities", method="GET"):
                request = urllib.request.Request(address + path + ("?" + question if question else ""), method=method)
                try:
                    with direct.open(request, timeout=10) as reply:
                        return reply.status, reply.headers["Content-Type"], reply.read().decode()
                except urllib.error.HTTPError as refused:
                    return refused.code, refused.headers["Content-Type"], refused.read().decode()

            def page(question=""):
                status, kind, text = ask(question)
                self.assertEqual((status, kind), (200, "application/json"), text)
                answer = json.loads(text)
                self.assertEqual(list(answer), ["rows", "next"])
                return answer["rows"], answer["next"]

            def numbers(question=""):
                rows, last = page(question)
                return [row["id"] for row in rows], last

            def kept(query):
                db = sqlite3.connect(archive)
                try:
                    return db.execute(query).fetchall()
                finally:
                    db.close()

            def note(statement):
                db = sqlite3.connect(archive)
                db.execute(statement)
                db.commit()
                db.close()

            # No archive yet, and then an archive with nothing of Prism's in it. There are
            # no rows, and the cursor to go on with is the one that was asked with.
            self.assertEqual(page(), ([], ""))
            self.assertEqual(page("after=0:5"), ([], "0:5"))
            self.gather_once(server, archive)
            self.assertTrue(archive.exists())
            self.assertEqual(page(), ([], ""))
            self.assertEqual(page("after=0:5"), ([], "0:5"))

            self.prism_like_the_servers(prism, {
                "prism_actions": [(1, "block-break"), (2, "block-place"), (3, "item-insert"), (4, "player-death"), (5, "sign-edit"), (6, "entity-death")],
                "prism_worlds": [(1, "dripcraft", "w-1"), (2, "dripcraft_nether", "w-2")],
                "prism_blocks": [
                    (1, "minecraft", "stone", "", "block.minecraft.stone"), (2, "minecraft", "oak_log", "[axis=y]", "block.minecraft.oak_log"),
                    (3, "minecraft", "air", "", "block.minecraft.air"), (4, "minecraft", "oak_sign", "[rotation=8]", "block.minecraft.oak_sign"),
                    (5, "minecraft", "tnt", "", "block.minecraft.tnt"), (6, "minecraft", "dirt", "", "block.minecraft.dirt"),
                ],
                "prism_items": [(1, "emerald", '{DataVersion:5023,id:"minecraft:emerald"}', None)],
                "prism_entity_types": [(1, "skeleton", "entity.minecraft.skeleton"), (2, "zombie", "entity.minecraft.zombie")],
                "prism_players": [(1, "Ada", self.ADA), (2, "Bo", self.BO)],
                "prism_causes": [(1, "fall")],
            }, [
                dict(timestamp=100, world_id=1, x=1, y=2, z=3, action_id=1, affected_block_id=1, cause_player_id=1, reversed=0),
                dict(timestamp=101, world_id=1, x=4, y=5, z=6, action_id=2, affected_block_id=2, replaced_block_id=3, cause_player_id=1, reversed=0),
                dict(timestamp=102, world_id=1, x=7, y=8, z=9, action_id=3, affected_item_id=1, affected_item_quantity=3, cause_player_id=1,
                     descriptor="Emerald", reversed=0),
                dict(timestamp=103, world_id=2, x=1, y=1, z=1, action_id=4, affected_player_id=2, cause_id=1, reversed=0),
                dict(timestamp=104, world_id=2, x=2, y=2, z=2, action_id=4, affected_player_id=2, cause_entity_type_id=1, reversed=0),
                dict(timestamp=105, world_id=1, x=3, y=3, z=3, action_id=5, affected_block_id=4, cause_player_id=1, descriptor="private words",
                     metadata='{"lines":["private words"]}', serializer_version=5023, serialized_data='{front_text:"private words"}', reversed=0),
                # Written before the row after it, and it happened later.
                dict(timestamp=110, world_id=1, action_id=6, affected_entity_type_id=2, cause_player_id=1, serializer_version=5023,
                     serialized_data="{CanPickUpLoot:1b}", reversed=0),
                dict(timestamp=107, world_id=1, action_id=1, affected_block_id=6, cause_block_id=5, metadata='{"note":"kept"}', reversed=1),
            ])
            self.assertIn("8 events", self.gather_once(server, archive))

            # Each row with names where Prism has numbers, and nothing that is empty.
            ada, bo = {"uuid": self.ADA, "name": "Ada"}, {"uuid": self.BO, "name": "Bo"}
            expected = [
                {"cursor": "0:1", "source": 0, "id": 1, "at": 100, "action": "block-break", "world": "dripcraft", "x": 1, "y": 2, "z": 3,
                 "block": "minecraft:stone", "causePlayer": ada, "reversed": False},
                {"cursor": "0:2", "source": 0, "id": 2, "at": 101, "action": "block-place", "world": "dripcraft", "x": 4, "y": 5, "z": 6,
                 "block": "minecraft:oak_log", "blockData": "[axis=y]", "replacedBlock": "minecraft:air", "causePlayer": ada, "reversed": False},
                {"cursor": "0:3", "source": 0, "id": 3, "at": 102, "action": "item-insert", "world": "dripcraft", "x": 7, "y": 8, "z": 9,
                 "item": "emerald", "quantity": 3, "causePlayer": ada, "reversed": False},
                # A death: who died is "player", and what of is a named cause or a creature.
                {"cursor": "0:4", "source": 0, "id": 4, "at": 103, "action": "player-death", "world": "dripcraft_nether", "x": 1, "y": 1, "z": 1,
                 "player": bo, "cause": "fall", "reversed": False},
                {"cursor": "0:5", "source": 0, "id": 5, "at": 104, "action": "player-death", "world": "dripcraft_nether", "x": 2, "y": 2, "z": 2,
                 "player": bo, "causeEntity": "skeleton", "reversed": False},
                {"cursor": "0:6", "source": 0, "id": 6, "at": 105, "action": "sign-edit", "world": "dripcraft", "x": 3, "y": 3, "z": 3,
                 "block": "minecraft:oak_sign", "blockData": "[rotation=8]", "causePlayer": ada, "reversed": False},
                {"cursor": "0:7", "source": 0, "id": 7, "at": 110, "action": "entity-death", "world": "dripcraft", "entity": "zombie",
                 "causePlayer": ada, "reversed": False},
                {"cursor": "0:8", "source": 0, "id": 8, "at": 107, "action": "block-break", "world": "dripcraft", "block": "minecraft:dirt",
                 "causeBlock": "minecraft:tnt", "reversed": True},
            ]

            # A page of them, then the next from where that one ended, then nothing more.
            self.assertEqual(page("limit=3"), (expected[:3], "0:3"))
            self.assertEqual(page("after=0:3"), (expected[3:], "0:8"))
            self.assertEqual(page("after=0:8"), ([], "0:8"))
            self.assertEqual(page("after="), (expected, "0:8"))

            # What a member may have written is not given out: not what is on a sign, not
            # the name of a thing, which they can change, and not what Prism keeps of
            # the thing itself. All of it is still in the archive.
            text = ask()[2]
            self.assertNotIn("private words", text)
            self.assertNotIn("Emerald", text)
            self.assertNotIn("kept", text)
            self.assertNotIn("CanPickUpLoot", text)
            for column in ("descriptor", "metadata", "serial"):
                self.assertNotIn(column, text)
            self.assertEqual(
                kept("SELECT descriptor, metadata, serialized_data FROM prism_activities WHERE activity_id = 6"),
                [("private words", '{"lines":["private words"]}', '{front_text:"private words"}')],
            )

            # The rows end before the first that happened later than "until". Row 8
            # happened at 107, but row 7 before it at 110, and no row is passed over.
            self.assertEqual(numbers("until=104"), ([1, 2, 3, 4, 5], "0:5"))
            self.assertEqual(numbers("until=108"), ([1, 2, 3, 4, 5, 6], "0:6"))
            self.assertEqual(numbers("after=0:6&until=108"), ([], "0:6"))
            self.assertEqual(numbers("until=99"), ([], ""))
            self.assertEqual(numbers("after=0:5&until=110&limit=2"), ([6, 7], "0:7"))
            self.assertEqual(numbers("until=&limit="), ([1, 2, 3, 4, 5, 6, 7, 8], "0:8"))

            # How many rows: at least one, at the most FEED_MOST, and FEED_ROWS unless asked.
            self.assertEqual(numbers("limit=0"), ([1], "0:1"))
            module.FEED_MOST = 2
            self.assertEqual(numbers("limit=100"), ([1, 2], "0:2"))
            module.FEED_MOST, module.FEED_ROWS = 5000, 4
            self.assertEqual(numbers(), ([1, 2, 3, 4], "0:4"))
            module.FEED_ROWS = 1000

            # A question that cannot be understood is refused, and the answer says why.
            for question in ("after=x", "after=5", "after=0:", "after=-1:5", "after=0:5:1", "until=soon", "until=-5",
                             "limit=many", "limit=1.5", "limit=-1"):
                status, kind, text = ask(question)
                self.assertEqual((status, kind), (400, "application/json"), question)
                self.assertEqual(list(json.loads(text)), ["error"], question)
            # There is nothing else at the feed's address, and nothing can be sent to it.
            for path in ("/", "/feed", "/feed/activities/", "/feed/positions", "/archive.db"):
                status, kind, text = ask(path=path)
                self.assertEqual((status, kind), (404, "application/json"), path)
            for method in ("POST", "PUT", "DELETE", "HEAD"):
                self.assertEqual(ask(method=method)[0], 501, method)

            # A row is given once what its numbers stand for has been copied as well.
            # The gathering notes how far that is, each time it has copied the names.
            self.assertEqual(kept("SELECT v FROM meta WHERE k = 'prism_named'"), [("0:8",)])
            note("UPDATE meta SET v = '0:2' WHERE k = 'prism_named'")
            self.assertEqual(numbers(), ([1, 2], "0:2"))
            note("DELETE FROM meta WHERE k = 'prism_named'")
            self.assertEqual(numbers(), ([], ""))
            self.gather_once(server, archive)
            self.assertEqual(numbers(), ([1, 2, 3, 4, 5, 6, 7, 8], "0:8"))

            # Rows are found by their number, in order. Nothing is sorted and no table is
            # read from end to end, however much the archive holds.
            db = sqlite3.connect(archive)
            statement, _ = module.feed_statement(db)
            plan = [step[3] for step in db.execute("EXPLAIN QUERY PLAN " + statement, (0, 3, 0, 8, 10))]
            db.close()
            self.assertTrue(plan[0].startswith("SEARCH a USING INDEX sqlite_autoindex_prism_activities_1 "), plan)
            for step in plan:
                self.assertNotIn("SCAN", step, plan)
                self.assertNotIn("TEMP B-TREE", step, plan)

            # A grief log that is replaced numbers its rows and its names from one again.
            # Its rows come after the old ones, each named by its own source's tables.
            prism.unlink()
            self.prism_like_the_servers(prism, {
                "prism_actions": [(1, "block-place")],
                "prism_worlds": [(1, "dripcraft", "w-1")],
                "prism_blocks": [(1, "minecraft", "glass", "", "block.minecraft.glass")],
                "prism_players": [(1, "Bo", self.BO)],
            }, [dict(timestamp=200, world_id=1, x=9, y=9, z=9, action_id=1, affected_block_id=1, cause_player_id=1, reversed=0)])
            self.assertIn("kept as source 1", self.gather_once(server, archive))
            newer = {"cursor": "1:1", "source": 1, "id": 1, "at": 200, "action": "block-place", "world": "dripcraft", "x": 9, "y": 9, "z": 9,
                     "block": "minecraft:glass", "causePlayer": bo, "reversed": False}
            self.assertEqual(page("after=0:8"), ([newer], "1:1"))
            self.assertEqual(page(), (expected + [newer], "1:1"))
            self.assertEqual(page("after=1:1"), ([], "1:1"))

    def test_feed_follows_a_prism_with_other_columns(self):
        # The archive's tables have whatever columns Prism has. The feed gives what is
        # there, and keeps back the words of a row whose kind it cannot tell.
        module = self.archive_script()
        with tempfile.TemporaryDirectory() as folder:
            server = pathlib.Path(folder) / "server"
            prism = server / "plugins" / "prism" / "prism.db"
            prism.parent.mkdir(parents=True)
            archive = pathlib.Path(folder) / "archive.db"
            module.ARCHIVE = archive.as_posix()
            db = sqlite3.connect(prism)
            db.executescript(f"""
                CREATE TABLE prism_actions (action_id INTEGER PRIMARY KEY, action varchar(25));
                CREATE TABLE prism_players (player_id INTEGER PRIMARY KEY, player varchar(32), player_uuid char(36));
                CREATE TABLE prism_activities (
                    activity_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp bigint, x INT, action_id bigint,
                    cause_player_id bigint, descriptor varchar(255), serialized_data clob);
                INSERT INTO prism_actions VALUES (1, 'block-break');
                INSERT INTO prism_players VALUES (1, 'Ada', '{self.ADA}');
                INSERT INTO prism_activities (timestamp, x, action_id, cause_player_id, descriptor, serialized_data) VALUES
                    (100, 5, 1, 1, 'Stone', '{{blob}}'), (101, 6, 9, NULL, 'words of an unknown kind', NULL);
            """)
            db.commit()
            db.close()
            self.assertIn("2 events", self.gather_once(server, archive))
            self.assertEqual(module.feed_page(), ([
                {"cursor": "0:1", "source": 0, "id": 1, "at": 100, "action": "block-break", "x": 5,
                 "causePlayer": {"uuid": self.ADA, "name": "Ada"}},
                {"cursor": "0:2", "source": 0, "id": 2, "at": 101, "x": 6},
            ], "0:2"))

    def test_archive_answers_the_feed_while_it_gathers(self):
        script = (ROOT / "manifests" / "archive.py").as_posix()
        with tempfile.TemporaryDirectory() as folder:
            server = pathlib.Path(folder) / "server"
            prism = server / "plugins" / "prism" / "prism.db"
            prism.parent.mkdir(parents=True)
            archive = pathlib.Path(folder) / "kept" / "archive.db"
            self.prism_like_the_servers(prism, {"prism_actions": [(1, "block-break")]}, [dict(timestamp=100, action_id=1)])
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def start(port):
                env = {
                    **os.environ, "SERVER_DIR": server.as_posix(), "ARCHIVE_FILE": archive.as_posix(),
                    "PLAYERS_URL": (server / "nobody.json").as_uri(), "FEED_PORT": str(port),
                }
                env.pop("ONCE", None)
                return subprocess.Popen([sys.executable, script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            def stop(process):
                # Asked to stop, as a pod is, it ends well.
                process.terminate()
                try:
                    out, err = process.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    raise
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(err, "")
                return out

            def wait_for(what):
                until = time.monotonic() + 30
                while time.monotonic() < until:
                    try:
                        found = what()
                        if found:
                            return found
                    except (OSError, ValueError, sqlite3.Error):
                        pass
                    time.sleep(0.1)
                self.fail("waited half a minute for the archive")

            def copied():
                db = sqlite3.connect(archive.as_uri() + "?mode=ro", uri=True)
                try:
                    return db.execute("SELECT count(*) FROM prism_activities").fetchone()[0]
                finally:
                    db.close()

            def answered():
                with direct.open(f"http://127.0.0.1:{port}/feed/activities", timeout=5) as reply:
                    rows = json.load(reply)["rows"]
                return rows if len(rows) == 2 else None

            # Something else has the feed's port. The gathering goes on without the feed.
            taken = socket.socket()
            taken.bind(("", 0))
            taken.listen()
            port = taken.getsockname()[1]
            process = start(port)
            try:
                self.assertEqual(wait_for(copied), 1)
            finally:
                out = stop(process)
                taken.close()
            self.assertIn("The feed cannot be answered", out)
            self.assertNotIn("Answering the feed", out)

            # With the port free, the rows it gathers are given out by the same process.
            db = sqlite3.connect(prism)
            db.execute("INSERT INTO prism_activities (timestamp, action_id) VALUES (101, 1)")
            db.commit()
            db.close()
            process = start(port)
            try:
                rows = wait_for(answered)
            finally:
                out = stop(process)
            self.assertEqual(
                [(row["cursor"], row["at"], row["action"]) for row in rows],
                [("0:1", 100, "block-break"), ("0:2", 101, "block-break")],
            )
            self.assertIn(f"Answering the feed on port {port}.", out)

    def test_default_deny_both_ways(self):
        spec = self.objects[("NetworkPolicy", "default-deny")]["spec"]
        self.assertEqual(spec["podSelector"], {})
        self.assertEqual(set(spec["policyTypes"]), {"Ingress", "Egress"})

    def ingress_by_source(self, policy):
        rules = self.objects[("NetworkPolicy", policy)]["spec"]["ingress"]
        for rule in rules:
            self.assertEqual(list(rule["from"][0]), ["podSelector"], policy)
            self.assertEqual(len(rule["from"]), 1, policy)
        return {r["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"]: r["ports"] for r in rules}

    def test_only_the_agent_the_map_proxy_and_the_archive_reach_the_server(self):
        self.assertEqual(self.ingress_by_source("minecraft-ingress"), {
            "playit-agent": [
                {"protocol": "TCP", "port": 25565},
                {"protocol": "UDP", "port": 24454},
                {"protocol": "UDP", "port": 19132},
            ],
            "web-map": [{"protocol": "TCP", "port": 8080}],
            "archive": [{"protocol": "TCP", "port": 8080}],
        })

    def test_only_the_agent_reaches_the_map_proxy(self):
        self.assertEqual(self.ingress_by_source("web-map-ingress"), {
            "playit-agent": [{"protocol": "TCP", "port": 8080}, {"protocol": "TCP", "port": 8443}],
        })

    def test_agent_can_reach_the_map_proxy(self):
        rule = next(
            r for r in self.egress("playit-agent-egress")
            if r["to"][0].get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "web-map"
        )
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 8080}, {"protocol": "TCP", "port": 8443}])

    def test_map_proxy_egress_is_the_map_and_https_only(self):
        rules = self.egress("web-map-egress")
        server = next(r for r in rules if r["to"][0].get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "minecraft")
        self.assertEqual(server["ports"], [{"protocol": "TCP", "port": 8080}])
        internet = next(r for r in rules if "ipBlock" in r["to"][0])
        self.assertEqual(internet["ports"], [{"protocol": "TCP", "port": 443}])

    def test_agent_can_reach_the_voice_and_bedrock_ports(self):
        rule = next(
            r for r in self.egress("playit-agent-egress")
            if r["to"][0].get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "minecraft"
        )
        self.assertIn({"protocol": "UDP", "port": 24454}, rule["ports"])
        self.assertIn({"protocol": "UDP", "port": 19132}, rule["ports"])

    def test_voice_port_is_the_same_everywhere(self):
        server = next(c for c in self.server["containers"] if c["name"] == "minecraft")
        ports = {p["name"]: (p["protocol"], p["containerPort"]) for p in server["ports"]}
        self.assertEqual(ports["voicechat"], ("UDP", 24454))

    def test_server_backups_and_discord_status_use_the_same_folder(self):
        # The Discord status keeps its message's ID there, so a restart edits the same message.
        for name in ("minecraft", "backup", "discord-status"):
            mount = next(m for m in self.container(name)["volumeMounts"] if m["name"] == "data")
            self.assertEqual((mount["mountPath"], mount["subPath"]), ("/data", "server"), name)

    def test_discord_status_script_is_mounted_where_it_is_run(self):
        container = self.container("discord-status")
        script = pathlib.PurePosixPath(container["command"][-1])
        mount = next(m for m in container["volumeMounts"] if m["mountPath"] == str(script.parent))
        volume = next(v for v in self.server["volumes"] if v["name"] == mount["name"])
        self.assertIn(script.name, self.objects[("ConfigMap", volume["configMap"]["name"])]["data"])

    def test_discord_webhook_comes_from_an_optional_secret_volume(self):
        # A volume that is not a subPath, rather than an environment variable, so a new
        # or changed webhook reaches the container without restarting the server.
        volume = next(v for v in self.server["volumes"] if v["name"] == "discord-webhook")
        self.assertEqual(volume["secret"], {"secretName": "discord-status", "optional": True})
        mount = next(m for m in self.container("discord-status")["volumeMounts"] if m["name"] == "discord-webhook")
        self.assertNotIn("subPath", mount)
        script = (ROOT / "manifests" / "discord-status.sh").read_text()
        self.assertIn(f"{mount['mountPath']}/webhook-url", script)

    def test_discord_status_never_pings_anyone(self):
        script = (ROOT / "manifests" / "discord-status.sh").read_text()
        self.assertIn("allowed_mentions: {parse: []}", script)

    def test_egress_never_reaches_private_networks(self):
        for policy in ("minecraft-egress", "minecraft-creative-egress", "playit-agent-egress", "uptime-check-egress", "web-map-egress"):
            blocks = [p["ipBlock"] for rule in self.egress(policy) for p in rule["to"] if "ipBlock" in p]
            self.assertEqual(len(blocks), 1, policy)
            self.assertEqual(blocks[0]["cidr"], "0.0.0.0/0")
            self.assertTrue(PRIVATE_RANGES <= set(blocks[0]["except"]), policy)

    def test_restart_warning_fits_in_the_shutdown_window(self):
        env = self.env()
        needed = int(env["STOP_SERVER_ANNOUNCE_DELAY"]["value"]) + int(env["STOP_DURATION"]["value"])
        self.assertGreater(self.server["terminationGracePeriodSeconds"], needed)

    def test_world_folder_is_set_in_git(self):
        self.assertIn("LEVEL", self.env())

    def test_anti_xray_is_on_and_hides_nether_ores(self):
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        ops = {op["$set"]["path"]: op["$set"]["value"] for patch in patches for op in patch["ops"]}
        self.assertIs(ops["$['anticheat']['anti-xray']['enabled']"], True)
        hidden = ops["$['anticheat']['anti-xray']['hidden-blocks']"]
        self.assertLessEqual({"diamond_ore", "deepslate_diamond_ore", "ancient_debris"}, set(hidden))

    def test_only_approved_plugins_are_installed(self):
        projects = dict(item.split(":") for item in self.env()["MODRINTH_PROJECTS"]["value"].split(","))
        self.assertEqual({"nbtapi", "prism", "simple-voice-chat", "chestsort-plus", "chunky", "geyser", "timberella", "discordsrv", "squaremap"}, set(projects))

    def test_pre_generation_continues_after_a_restart(self):
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        ops = {op["$set"]["path"]: op["$set"]["value"] for patch in patches for op in patch["ops"]}
        self.assertIs(ops["$['continue-on-restart']"], True)

    def test_players_get_sorting_and_nothing_else(self):
        text = (ROOT / "manifests" / "player-permissions.yml").read_text()
        grant = yaml.safe_load(text)["lushacres.player"]
        self.assertIs(grant["default"], True)
        self.assertEqual(grant["children"], {"chestsort.use": True})

    def test_permissions_file_is_where_bukkit_is_told_to_look(self):
        patches = json.loads((ROOT / "manifests" / "tuning-patches.json").read_text())["patches"]
        ops = {op["$set"]["path"]: op["$set"]["value"] for patch in patches for op in patch["ops"]}
        self.assertEqual(ops["$['settings']['permissions-file']"], "config/player-permissions.yml")
        server = next(c for c in self.server["containers"] if c["name"] == "minecraft")
        mounts = {m["mountPath"]: m for m in server["volumeMounts"]}
        self.assertEqual(mounts["/config/player-permissions.yml"]["subPath"], "player-permissions.yml")

    def test_uptime_check_secret_stays_out_of_git(self):
        container = self.uptime["containers"][0]
        for entry in container["env"]:
            ref = entry["valueFrom"]["secretKeyRef"]
            self.assertEqual(ref["name"], "uptime-check")
            self.assertIs(ref["optional"], True)

    def test_server_egress_is_https_only(self):
        rule = next(r for r in self.egress("minecraft-egress") if "ipBlock" in r["to"][0])
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 443}])

    # The creative server.

    def creative_container(self, name="minecraft"):
        return next(c for c in self.creative["containers"] if c["name"] == name)

    def creative_env(self, container="minecraft"):
        return {e["name"]: e for e in self.creative_container(container)["env"]}

    def test_creative_is_a_flat_world_where_everyone_is_in_creative_mode(self):
        env = self.creative_env()
        self.assertEqual(env["LEVEL_TYPE"]["value"], "minecraft:flat")
        self.assertEqual(env["MODE"]["value"], "creative")
        self.assertEqual(env["FORCE_GAMEMODE"]["value"], "TRUE")
        self.assertEqual(env["DIFFICULTY"]["value"], "peaceful")

    def test_creative_has_the_same_access_rules_as_survival(self):
        survival, creative = self.env(), self.creative_env()
        for name in (
            "ONLINE_MODE", "ENABLE_WHITELIST", "ENFORCE_WHITELIST", "ENFORCE_SECURE_PROFILE",
            "HIDE_ONLINE_PLAYERS", "ENABLE_QUERY", "ENABLE_COMMAND_BLOCK", "USES_PROXY_PROTOCOL",
        ):
            self.assertEqual(creative[name]["value"], survival[name]["value"], name)
        paper = yaml.safe_load((ROOT / "manifests" / "creative-paper-global.yml").read_text())
        self.assertIs(paper["proxies"]["proxy-protocol"], True)

    def test_creative_runs_the_same_builds_as_survival(self):
        # Players use one client for both, and each plugin build was checked against
        # one Paper build, so the two servers are bumped together.
        survival, creative = self.env(), self.creative_env()
        for name in ("minecraft", "backup", "discord-status"):
            self.assertEqual(self.creative_container(name)["image"], self.container(name)["image"], name)
        for name in ("TYPE", "VERSION", "PAPER_BUILD", "PLUGINS"):
            self.assertEqual(creative[name]["value"], survival[name]["value"], name)
        builds = dict(item.split(":") for item in survival["MODRINTH_PROJECTS"]["value"].split(","))
        projects = dict(item.split(":") for item in creative["MODRINTH_PROJECTS"]["value"].split(","))
        self.assertEqual(set(projects), {"geyser", "discordsrv"})
        for name, build in projects.items():
            self.assertEqual(build, builds[name], name)

    def test_creative_redstone_behaves_as_it_does_on_survival(self):
        def redstone(patches):
            patch = next(p for p in patches if p["file"] == "/data/config/paper-world-defaults.yml")
            return next(op["$set"]["value"] for op in patch["ops"] if "redstone-implementation" in op["$set"]["path"])
        self.assertEqual(redstone(self.creative_patches), redstone(self.patches))

    def test_creative_discord_bot_is_not_the_survival_bot(self):
        # A server ignores what its own bot posted, so sharing a bot would stop chat
        # from one server reaching the other.
        ref = self.creative_env()["DISCORDSRV_TOKEN"]["valueFrom"]["secretKeyRef"]
        self.assertEqual((ref["name"], ref["key"]), ("discord-bot-creative", "token"))
        self.assertIs(ref["optional"], True)
        self.assertNotEqual(ref["name"], self.env()["DISCORDSRV_TOKEN"]["valueFrom"]["secretKeyRef"]["name"])

    def test_creative_discord_is_restricted_like_survival(self):
        for file in ("/data/plugins/DiscordSRV/config.yml", "/data/plugins/DiscordSRV/messages.yml"):
            survival = next(p for p in self.patches if p["file"] == file)
            creative = next(p for p in self.creative_patches if p["file"] == file)
            self.assertEqual(creative["ops"], survival["ops"], file)

    def test_creative_status_message_has_its_own_name_and_webhook(self):
        name = self.creative_env("discord-status")["SERVER_NAME"]["value"]
        self.assertNotEqual(name, "Lush Acres")
        # Players see one name, in the server list and in Discord.
        self.assertEqual(name, self.creative_env()["MOTD"]["value"])
        volume = next(v for v in self.creative["volumes"] if v["name"] == "discord-webhook")
        self.assertEqual(volume["secret"], {"secretName": "discord-status-creative", "optional": True})
        mount = next(m for m in self.creative_container("discord-status")["volumeMounts"] if m["name"] == "discord-webhook")
        self.assertNotIn("subPath", mount)
        script = (ROOT / "manifests" / "discord-status.sh").read_text()
        self.assertIn(f"{mount['mountPath']}/webhook-url", script)

    def test_creative_config_is_mounted_where_the_image_reads_it(self):
        mounts = {m["mountPath"]: m for m in self.creative_container()["volumeMounts"]}
        data = self.objects_with_prefix("ConfigMap", "creative-config")["data"]
        for path in (self.creative_env()["PATCH_DEFINITIONS"]["value"], "/config/paper-global.yml"):
            self.assertEqual(mounts[path]["subPath"], pathlib.PurePosixPath(path).name)
            self.assertIn(mounts[path]["subPath"], data)

    def test_creative_has_its_own_volumes_mounted_whole(self):
        # No subPath: local-path volumes ignore fsGroup, so a folder inside one would
        # have to be created by hand before the pod starts.
        claims = self.objects[("StatefulSet", "minecraft-creative")]["spec"]["volumeClaimTemplates"]
        self.assertEqual([c["metadata"]["name"] for c in claims], ["data", "backups"])
        for name in ("minecraft", "backup", "discord-status"):
            mount = next(m for m in self.creative_container(name)["volumeMounts"] if m["name"] == "data")
            self.assertEqual(mount["mountPath"], "/data", name)
            self.assertNotIn("subPath", mount, name)

    def test_creative_secrets_are_referenced_not_inlined(self):
        for container in ("minecraft", "backup", "discord-status"):
            self.assertIn("secretKeyRef", self.creative_env(container)["RCON_PASSWORD"]["valueFrom"])

    def test_creative_heap_fits_in_the_pod_limit(self):
        resources = self.creative_container()["resources"]
        heap = int(self.creative_env()["MEMORY"]["value"].rstrip("G"))
        limit = int(resources["limits"]["memory"].rstrip("Gi"))
        self.assertGreaterEqual(limit - heap, 2)
        self.assertEqual(resources["requests"]["memory"], resources["limits"]["memory"])
        self.assertNotIn("cpu", resources["limits"])

    def test_creative_reserves_almost_no_cpu(self):
        # The node's CPU is almost fully reserved, and firecrawl-api needs 1 core
        # unreserved to roll out an update. Check the node before raising this.
        millicores = sum(int(c["resources"]["requests"]["cpu"].rstrip("m")) for c in self.creative["containers"])
        self.assertLessEqual(millicores, 40)

    def test_creative_restart_warning_fits_in_the_shutdown_window(self):
        env = self.creative_env()
        needed = int(env["STOP_SERVER_ANNOUNCE_DELAY"]["value"]) + int(env["STOP_DURATION"]["value"])
        self.assertGreater(self.creative["terminationGracePeriodSeconds"], needed)

    def test_creative_tunnel_origin_is_a_fixed_address_of_its_own(self):
        service = self.objects[("Service", "minecraft-creative")]["spec"]
        self.assertEqual(service["clusterIP"], "10.96.25.67")
        self.assertEqual(service.get("type", "ClusterIP"), "ClusterIP")
        self.assertEqual(
            [(p["protocol"], p["port"]) for p in service["ports"]],
            [("TCP", 25565), ("UDP", 19132)],
        )
        fixed = [obj["spec"]["clusterIP"] for (kind, _), obj in self.objects.items() if kind == "Service" and "clusterIP" in obj["spec"]]
        self.assertEqual(len(fixed), 3)
        self.assertEqual(len(fixed), len(set(fixed)))

    def test_only_the_agent_reaches_the_creative_server(self):
        self.assertEqual(self.ingress_by_source("minecraft-creative-ingress"), {
            "playit-agent": [{"protocol": "TCP", "port": 25565}, {"protocol": "UDP", "port": 19132}],
        })

    def test_agent_can_reach_the_creative_server(self):
        rule = next(
            r for r in self.egress("playit-agent-egress")
            if r["to"][0].get("podSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") == "minecraft-creative"
        )
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 25565}, {"protocol": "UDP", "port": 19132}])

    def test_creative_egress_is_https_only(self):
        rule = next(r for r in self.egress("minecraft-creative-egress") if "ipBlock" in r["to"][0])
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 443}])


if __name__ == "__main__":
    unittest.main()
