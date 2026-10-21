# ADR 0075: explicit permissions at file and directory creation

Status: accepted. Extends ADR 0043 (blocking filesystem). No syntax change.

## Evidence

The migrated tarn-ui launcher must keep private captures: a directory with
mode 0700 and exclusive files with mode 0600, private from the moment they
are created. `fs` hardcoded `open(..., 0666)` and `mkdir(..., 0777)` and had
no chmod. Under umask 022, Codex's probe produced 0755/0644. A chmod after
creation would leave a window in which another user can open the path.

## Decision

- `File.create_new_with_mode(path &string, mode u32) Result<File, io.Error>`:
  `O_CREAT | O_EXCL` with `mode` in the same `open` call.
- `create_dir_with_mode(path &string, mode u32) Result<void, io.Error>`:
  `mkdir(path, mode)`.
- The existing `_open`/`_mkdir` intrinsics take the permission bits as an
  extra argument; the existing functions pass 0o666/0o777, so their behaviour
  is unchanged. There is no new owner and no new intrinsic.
- Only `0o777` bits are accepted, checked in Tarn and again in the bridge.
  Setuid, setgid and sticky bits are InvalidInput, so a mistyped mode can never
  quietly create a privileged file.
- umask keeps its standard Linux meaning (it only removes bits). Overriding it
  would be hidden global state; private modes are unaffected by any usual
  umask.

## Alternatives

- A Rust-style `OpenOptions` builder: no evidence yet beyond exclusive private
  files and directories.
- `set_permissions` after creation: racy for the private-data use case. It
  could be added later for files that must change mode.
