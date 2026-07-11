# Tools reference

The agent ships with 50+ tools grouped by domain. Tools marked ⚠ are
*dangerous* and require confirmation unless `/auto` is enabled.

## Shell & files
- `run_shell` ⚠ — execute a shell command
- `read_file`, `write_file` ⚠, `append_file`, `list_dir`

## File editing
- `view_lines`, `replace_in_file` ⚠, `insert_line` ⚠, `delete_lines` ⚠

## Search
- `find_files`, `grep`, `count_lines`

## Git
- `git_status`, `git_diff`, `git_log`, `git_branch`

## Code execution
- `run_python` ⚠ — run a Python snippet in an isolated subprocess

## HTTP & web
- `http_get`, `http_request`, `http_json`, `fetch_text`

## Data
- `json_query`, `json_format`, `csv_to_json`, `csv_summary`

## Encoding & hashing
- `b64_encode`, `b64_decode`, `hex_encode`, `url_encode`, `url_decode`, `hash_text`

## Text processing
- `text_stats`, `change_case`, `sort_lines`, `text_diff`, `dedupe_lines`

## Math & time
- `calculate`, `now`, `date_diff`

## System
- `system_info`, `get_env`, `disk_usage`, `which`

## Network
- `dns_resolve`, `public_ip`, `port_check`, `ping`

## Random / generation
- `gen_uuid`, `gen_password`, `gen_lorem`, `roll_dice`, `random_choice`

## Archive
- `zip_create` ⚠, `zip_list`, `zip_extract` ⚠
