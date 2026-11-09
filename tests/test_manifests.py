"""Run: python -m unittest discover -s tests (requires PyYAML and kubectl)."""
import json
import pathlib
import subprocess
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
        cls.agent = cls.objects[("Deployment", "playit-agent")]["spec"]["template"]["spec"]
        cls.uptime = cls.objects[("CronJob", "uptime-check")]["spec"]["jobTemplate"]["spec"]["template"]["spec"]

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
        for pod in (self.server, self.agent, self.uptime):
            self.assertIs(pod["automountServiceAccountToken"], False)
            self.assertIs(pod["securityContext"]["runAsNonRoot"], True)
            self.assertEqual(pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
            for container in pod["containers"]:
                context = container["securityContext"]
                self.assertIs(context["allowPrivilegeEscalation"], False)
                self.assertIs(context["readOnlyRootFilesystem"], True)
                self.assertEqual(context["capabilities"]["drop"], ["ALL"])

    def test_images_are_pinned_by_digest(self):
        for pod in (self.server, self.agent, self.uptime):
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
        })
        patch = next(p for p in patches if p["file"] == "/data/plugins/DiscordSRV/messages.yml")
        self.assertEqual({op["$set"]["value"] for op in patch["ops"]}, {""})

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
            [("TCP", 25565), ("UDP", 24454), ("UDP", 19132)],
        )

    def test_default_deny_both_ways(self):
        spec = self.objects[("NetworkPolicy", "default-deny")]["spec"]
        self.assertEqual(spec["podSelector"], {})
        self.assertEqual(set(spec["policyTypes"]), {"Ingress", "Egress"})

    def test_only_the_agent_reaches_only_the_public_ports(self):
        rules = self.objects[("NetworkPolicy", "minecraft-ingress")]["spec"]["ingress"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(
            rules[0]["ports"],
            [
                {"protocol": "TCP", "port": 25565},
                {"protocol": "UDP", "port": 24454},
                {"protocol": "UDP", "port": 19132},
            ],
        )
        self.assertEqual(
            rules[0]["from"],
            [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "playit-agent"}}}],
        )

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
        for policy in ("minecraft-egress", "playit-agent-egress", "uptime-check-egress"):
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
        self.assertEqual({"nbtapi", "prism", "simple-voice-chat", "chestsort-plus", "chunky", "geyser", "timberella", "discordsrv"}, set(projects))

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


if __name__ == "__main__":
    unittest.main()
