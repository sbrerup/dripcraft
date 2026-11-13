"""Run: python -m unittest discover -s tests (requires PyYAML and kubectl).

The test that runs player-stats.sh also needs sh and jq, and is skipped without them.
"""
import gzip
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

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
        for pod in (self.server, self.creative, self.agent, self.uptime, self.web_map):
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
        for pod in (self.server, self.creative, self.agent, self.uptime, self.web_map):
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
        self.assertIn('<a href="map/">', (ROOT / "manifests" / "player-stats.html").read_text(encoding="utf-8"))
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
        self.assertEqual(page["items"], [{"key": "index.html", "path": "index.html"}])
        self.assertIn("index.html", self.objects_with_prefix("ConfigMap", "player-stats")["data"])
        # The page asks for data/stats.json beside itself.
        self.assertIn("handle_path /data/* {", self.caddyfile)
        self.assertIn('fetch("data/stats.json"', (ROOT / "manifests" / "player-stats.html").read_text(encoding="utf-8"))

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

    def test_stats_page_loads_nothing_from_other_sites(self):
        page = (ROOT / "manifests" / "player-stats.html").read_text(encoding="utf-8")
        # Skins and flags are copies the script fetched. A link the reader may follow is fine.
        self.assertNotRegex(page, r"src\s*=\s*[\"'](?:https?:)?//")
        self.assertNotRegex(page, r"<link|@import|url\(")

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

    def test_only_the_agent_and_the_map_proxy_reach_the_server(self):
        self.assertEqual(self.ingress_by_source("minecraft-ingress"), {
            "playit-agent": [
                {"protocol": "TCP", "port": 25565},
                {"protocol": "UDP", "port": 24454},
                {"protocol": "UDP", "port": 19132},
            ],
            "web-map": [{"protocol": "TCP", "port": 8080}],
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
        fixed = [obj["spec"]["clusterIP"] for (kind, _), obj in self.objects.items() if kind == "Service"]
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
