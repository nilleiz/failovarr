# Changelog

All notable changes are documented here.

## 0.8.5 — Development Preview

- Version-3 bundles encrypt Dispatcharr user accounts, API keys, password hashes
  and account relationships with AES-GCM using a key derived from the cluster
  secret.
- Followers can select Main users and choose to follow Main's active state or
  force each imported user active or inactive. New Main users are selected by
  default; local-only users are preserved and never block imports.
- Replaced the global client-identity mismatch check with transactional
  username-based user reconciliation that retains local primary keys.
- Followers continue to accept bundle formats 1 and 2 without user changes.

## 0.8.4 — Development Preview

- SFTP storage now treats an empty bundle location as an expected "no Main
  bundle" state instead of an internal runtime error.
- A Follower can recover the newest complete signed SFTP bundle when only its
  `latest.json` pointer is missing, and Main verifies that its pointer resolves
  to the exact published envelope before reporting export success.

## 0.8.3 — Development Preview

- A Follower now blocks every import path when its global M3U Hash Key differs
  from the signed Main setting, and can deliberately adopt that exact value
  before retrying the same bundle.

## 0.8.2 — Development Preview

- Follower status now shows both the local import time and the signed export
  time of the Main bundle that was imported.

## 0.8.1 — Development Preview

- Followers can deliberately reapply their current verified Main bundle to
  repair unprotected local drift without permitting an older or changed bundle.

## 0.8.0 — Development Preview

- Version-2 signed bundles now replicate Stream and channel-group/account
  availability lifecycle (`is_stale` and `last_seen`).
- An upgraded Follower rejects a legacy version-1 bundle when its selected
  scope needs lifecycle replication, with an actionable Main re-export
  message.

## 0.7.1 — Development Preview

- Channel Stream assignments now reconcile by their Channel-and-Stream
  identity when third-party plugins recreate their join rows.
- Adds a Follower-local option to mirror Main stream assignments exactly
  without enabling deletion across every replicated domain.

## 0.7.0 — Development Preview

- Product identity changed from the private Dispatcharr Redundancy laboratory
  project to **Failovarr**.
- Adds compatible import of legacy configuration profiles and node-local
  configuration migration.
- Publishes the initial public documentation, feature catalogue and selective
  qualification policy.

This is a work in progress and is not production ready.
