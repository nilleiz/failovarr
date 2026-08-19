import logging
import unittest
from unittest.mock import patch

from failovarr.config import DOMAIN_GROUPS
from failovarr.setup_assistant import SETUP_HTML, SetupServer


class SetupHtmlTests(unittest.TestCase):
    def test_ui_is_english_and_uses_single_management_port_contract(self):
        self.assertIn('<html lang="en">', SETUP_HTML)
        self.assertIn("Copy shared configuration", SETUP_HTML)
        self.assertIn('name="redundancy_mode"', SETUP_HTML)
        self.assertNotIn('name="deployment_mode"', SETUP_HTML)
        self.assertNotIn('name="client_access_mode"', SETUP_HTML)
        self.assertIn("Initialize follower from Main", SETUP_HTML)
        self.assertNotIn("9193", SETUP_HTML)

    def test_role_and_domain_specific_sections_exist(self):
        self.assertIn('class="card leader-only"', SETUP_HTML)
        self.assertIn('class="card follower-only hidden"', SETUP_HTML)
        self.assertIn('id="recordProtection"', SETUP_HTML)
        self.assertIn("meta.local_protection_domains", SETUP_HTML)
        self.assertIn('id="followerImportProfile"', SETUP_HTML)
        self.assertIn('id="followerScope"', SETUP_HTML)
        self.assertIn('id="bundleInfo"', SETUP_HTML)
        self.assertIn('id="refreshBundleInfo"', SETUP_HTML)
        self.assertIn("x.new_record_policy_fields[d]", SETUP_HTML)
        self.assertNotIn('id="newOutputPolicy"', SETUP_HTML)

    def test_local_protection_visibility_tracks_the_effective_follower_scope(self):
        self.assertIn('data-protection-domain="${esc(d)}"', SETUP_HTML)
        self.assertIn("document.querySelectorAll('[data-protection-domain]')", SETUP_HTML)
        self.assertIn("protectedDomains.has(e.dataset.protectionDomain)", SETUP_HTML)
        self.assertNotIn(".filter(d=>protectedScope.includes(d))", SETUP_HTML)

    def test_cold_standby_keeps_explicit_autostart_but_hides_import_switches(self):
        self.assertIn('id="coldAutomationNotice"', SETUP_HTML)
        self.assertIn('id="automationControls"', SETUP_HTML)
        self.assertNotIn('id="onlineAutomation"', SETUP_HTML)
        conditional = SETUP_HTML.split("function conditional(){", 1)[1].split(
            "function syncDomains(){", 1,
        )[0]
        self.assertNotIn("set('auto_start',true)", conditional)
        self.assertIn("settings.auto_start===undefined", SETUP_HTML)
        self.assertIn("set('auto_start',true)", SETUP_HTML)
        self.assertIn("set('import_on_start',true)", SETUP_HTML)
        self.assertIn("set('automatic_apply',false)", SETUP_HTML)
        self.assertIn("cold_standby_disabled", SETUP_HTML)
        self.assertIn("meta?.automation_text", SETUP_HTML)

    def test_no_visual_section_numbering_remains(self):
        for heading in (
            "1. Node and deployment", "2. Replication transport and storage",
            "3. Data replicated from Main", "4. Output Profiles kept local",
        ):
            self.assertNotIn(heading, SETUP_HTML)

    def test_action_results_are_placed_next_to_their_controls(self):
        storage_button = SETUP_HTML.index('id="testStorage"')
        storage_result = SETUP_HTML.index('id="storageResult"')
        next_section = SETUP_HTML.index('id="followerScope"')
        self.assertLess(storage_button, storage_result)
        self.assertLess(storage_result, next_section)

        import_button = SETUP_HTML.index('id="importLatest"')
        import_result = SETUP_HTML.index('id="importResult"')
        self.assertLess(import_button, import_result)

    def test_follower_profile_import_is_directly_below_node_deployment(self):
        node_section = SETUP_HTML.index("Node and deployment")
        profile_section = SETUP_HTML.index('id="followerImportProfile"')
        storage_section = SETUP_HTML.index("Replication transport and storage")
        self.assertLess(node_section, profile_section)
        self.assertLess(profile_section, storage_section)

    def test_role_is_first_editable_control_and_enabled_cold_banner_is_removed(self):
        self.assertLess(SETUP_HTML.index('name="role"'), SETUP_HTML.index('name="node_id"'))
        self.assertNotIn("Automatic replication is enabled.", SETUP_HTML)
        self.assertNotIn("Data exported by Main", SETUP_HTML)

    def test_content_only_preset_and_human_readable_result_rendering_exist(self):
        self.assertIn('value="iptv_content"', SETUP_HTML)
        self.assertIn("M3U, EPG and Channels only", SETUP_HTML)
        self.assertIn("function renderResult", SETUP_HTML)
        self.assertIn("Technical details", SETUP_HTML)
        self.assertIn("/api/bundle-info", SETUP_HTML)
        self.assertIn("Stores sequences, hashes, replay/apply state", SETUP_HTML)

    def test_current_bundle_state_keeps_preview_but_disables_import(self):
        self.assertIn("x.status==='current'", SETUP_HTML)
        self.assertIn("Already up to date", SETUP_HTML)
        self.assertIn("response.status==='waiting'?'warn'", SETUP_HTML)
        self.assertIn("button.dataset.bundleDisabled", SETUP_HTML)

    def test_follower_scope_uses_dispatcharr_display_order(self):
        self.assertEqual(list(DOMAIN_GROUPS), ["channels", "m3u_epg", "logos", "settings"])
        self.assertIn("Object.entries(x.domain_groups)", SETUP_HTML)

    def test_all_expected_setup_routes_are_used(self):
        for route in (
            "/api/config", "/api/test-storage", "/api/profile",
            "/api/profile/import", "/api/preview", "/api/import",
            "/api/initialize", "/api/status", "/api/export", "/api/sftp/host-key",
            "/api/sftp/trust-host-key", "/api/sftp/private-key", "/api/bundle-info",
        ):
            self.assertIn(route, SETUP_HTML)

    def test_readiness_fails_closed_when_its_runtime_check_errors(self):
        server = SetupServer({}, logging.getLogger("test"))
        with patch.object(server, "_engine", side_effect=RuntimeError("synthetic failure")):
            self.assertFalse(server.is_client_ready())


if __name__ == "__main__":
    unittest.main()
