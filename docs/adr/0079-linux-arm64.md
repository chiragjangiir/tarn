# ADR 0079: native Linux ARM64 support

Status: implemented; extends the platform boundaries of ADRs 0027 and 0047.

## Evidence and scope

Tarn applications need separate Linux x86_64 and ARM64 builder variants. The
backend already selects its host ISA through Cranelift, and the Linux runtime
uses C and Linux APIs rather than x86 assembly. Blocking every non-x86 host is
therefore an artificial restriction, but removing that restriction alone would
leave ABI, testing and distribution assumptions unchecked.

Support little-endian Linux AArch64 with 64-bit pointers and LP64 C, alongside
Linux x86_64. ARM here means ARM64, not ARMv7/32-bit or big-endian ARM. Compilers
build native programs for their own architecture. Cross-compilation and Windows
are outside this change.

## Decisions

- Keep the existing host-arch Cranelift feature and native ISA selection. Its
  native calling convention drives both Tarn internal calls and scalar C FFI:
  System V on x86_64, AAPCS64 on AArch64. No frontend or ownership rule changes.
- Enable PIC emission on AArch64. Cranelift 0.125.3's object writer does not
  implement the non-PIC `Aarch64AdrPrelPgHi21` symbol-address relocation; the
  native ABI regression catches that panic. PIC uses supported ELF GOT
  relocations, while x86_64 retains its existing setting and both targets keep
  the existing non-PIE final executable link.
- Keep the single 64-bit Tarn layout authority. Both supported targets use
  eight-byte pointers/usize and the existing primitive sizes and alignments.
  Runtime static assertions reject non-LP64 C builds. Protocol wire layouts do
  not become host-dependent. Cranelift loads/stores without the aligned flag
  continue to permit unaligned copies.
- Reuse the Linux runtime: epoll, timerfd, pidfd, pthreads and libcurl remain the
  same APIs. Kernel syscall numbers come from the target headers, not x86
  constants. Runtime object cache keys include the host architecture and OS, so
  shared caches never mix target objects. No platform abstraction layer is
  added without a second OS.
- Run the full verified workspace tests, CLI and native container smoke and installer checks on
  separate Ubuntu 22.04 x86_64 and ARM64 runners. A native C ABI regression
  exercises signed/unsigned narrow integers, booleans, floating-point arguments
  and enough arguments to cross both architectures' register/stack boundaries.
- Release separate `linux-x86_64` and `linux-aarch64` archives. Combine their
  checksums only after both builds succeed. The standalone installer detects
  its architecture, accepts aarch64/arm64 aliases and rejects other machines.
  It verifies exactly one selected checksum rather than requiring archives for
  other platforms to have been downloaded.
- Build and smoke-test containers on the corresponding native runners. Publish
  explicit `VERSION-amd64` and `VERSION-arm64` variants, then a single immutable
  `VERSION` index referencing tested image digests. Promote the complete index
  to latest; local docker tag/push would drop the other architecture.
- Discover architectures from release assets, so retrying an older x86-only
  release remains possible without modifying it or pretending it has ARM code.

## Alternatives and limits

Cross-compiling or emulating every CI job would complicate the compiler/linker
boundary and provide less direct evidence than native runners. Local QEMU is
useful for development checks; hosted native ARM execution remains the release
gate. Linux ARM64 is not Apple Silicon/macOS support. The existing scalar-only
C FFI and glibc 2.35 release baseline remain unchanged.

Existing published tags, archives and images stay immutable. ARM artifacts
start with the next release built from this change; 0.0.7 is not retrofitted.
