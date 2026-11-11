# Project rules

This project targets Windows and Python 3.13. Keep runtime data under LOCALAPPDATA or an explicitly configured external directory. Never commit keys, DPAPI blobs, auth files, catalogs containing provider instructions, chat pins, logs, or memory summaries. Do not change a user's live Codex configuration or startup task during development. Run the offline test before proposing a release. Use the existing _gpt filename convention for generated implementation files.
