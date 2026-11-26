# Isolated Linux SDK for agents

The isolated runner executes a Linux native SDK command inside Docker, exposing both the SDK and the target project read-only so the compiler and compiled program share a single disposable container. Host execution requires a Linux Docker engine and Python 3.11+. The container bundles the static SDK and its packaged Zig toolchain.

## Usage

From a checkout, extract the verified Linux SDK into `installed/wysna-native` and build the local image using that directory as the Docker build context. The Dockerfile starts from `scratch` and requires no base-image download.

```sh
image="$(docker build --quiet --network=none --file packaging/container/Dockerfile installed/wysna-native)"
python3 tools/run_isolated.py --image "$image" --project ./my-project -- check /project/src/main.wysna
python3 tools/run_isolated.py --image "$image" --project ./my-project -- build /project/src/main.wysna -o /work/program
python3 tools/run_isolated.py --image "$image" --project ./my-project --timeout 180 -- run /project/src/main.wysna
```

`--project` mounts the selected workspace read-only at `/project`. Prepare a directory containing all sources, locked dependencies, and required task inputs beforehand. Write any temporary build outputs under `/work`; the build phase verifies the binary inside the container, and the runner removes the container and workspace scratch area on exit.

## Limits and outputs

- The SDK runs as UID `65532` with Linux capabilities dropped, a read-only root filesystem, and `no-new-privileges`. Nested host mounts inside `/project` are excluded.
- Networking is disabled and proxy credentials are not forwarded. Processes and threads share limits of 128 PIDs, 1 GiB of RAM, and a single-CPU quota, with only bounded temporary mounts writable.
- The `wysna.isolated-run/1` JSON output reports `status`, `exit_code`, `container_removed`, and the first 64 KiB of `stdout` and `stderr`. Out-of-memory and timeout conditions emit dedicated statuses: a timeout terminates the container with exit code `124`, and a container removal failure emits `cleanup_failed` alongside the container ID.
- The default timeout budget accounts for a cold Zig cache startup, using a fresh per-invocation temporary cache.
- The Docker daemon, container image, and host kernel belong to the trusted computing base. To isolate mutually hostile tenants, run inside a dedicated VM. With a remote Docker daemon, `--project` is resolved on the daemon host; prefer a local daemon when preparing workspaces.
- This profile targets programs compatible with the offline Linux SDK. Wysna `--sandbox` capability flags can be layered as application-level controls on supported commands; both compilation and execution remain within the same container boundary.

CI builds the image from the Linux SDK archive and verifies `check`, `build`, `run`, read-only directories, absence of leaked host environment variables, and container removal upon deadline expiration.
