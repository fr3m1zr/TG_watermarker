# TG_watermarker

Telegram bot that reads image EXIF metadata and adds a watermark/details panel.
The macOS path uses Apple's `container` CLI directly; Docker Desktop and
`docker compose` are not required for the primary workflow.

## macOS / Apple container

Prerequisites:

- Apple Silicon Mac running macOS 26 or later. User-defined Apple container
  networks are a macOS 26 feature, and the two services need to communicate
  over that network.
- Apple's [`container` CLI](https://github.com/apple/container) installed and
  available as `container` in `PATH`.
- Network access for the first image build/pull. The Dockerfile uses the
  Debian-listed Taiwan mirror `debian.cs.nycu.edu.tw` for Bookworm packages
  and the official Debian security service for security updates.

Check the installation before starting:

```sh
container --version
container system status
```

The script starts the Apple container service and image builder if either is
not already running. It only manages these project-owned names:

- containers: `tg-watermarker-telegram-bot-api`, `tg-watermarker-bot`
- network: `tg-watermarker-net`
- data directory: `.container-data/telegram-bot-api` by default (configurable
  with `TELEGRAM_API_DATA_DIR`)

It does not use `container stop --all`, `container delete --all`, volume/image
prune, or any Docker command.

## Configuration

Create the ignored secrets file and edit the three required values:

```sh
cp .env.example .env
```

Required values:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`: credentials for the local
  `telegram-bot-api` server.
- `TELEGRAM_BOT_TOKEN`: the bot token.

`assets/signature.example.png` is used as a fallback. To use a private custom
signature, copy it to `assets/signature.png` (already ignored by Git), or put
another file under `assets/` and set `SIGNATURE_IMAGE_FILE` to its filename.

`MAX_FILE_SIZE_MB` limits the compressed Telegram file size before it is
downloaded; the provided deployment default remains `100` MB. This is a
compressed-file limit, not a resolution or memory limit.
`MAX_IMAGE_PIXELS` is a separate, non-secret decoded-image safety limit. It is
checked from the image header before the full pixel data is loaded; it defaults
to `100,000,000` pixels (100 MP). The common `9984x6656` input is
`66,453,504` pixels and is therefore allowed by default. A file can be below
the 100 MB compressed-size limit and still be rejected when its decoded
resolution exceeds the pixel limit.
`BOT_MEMORY_LIMIT` is the non-secret runtime memory ceiling for the bot
container. It defaults to `2G`; Apple startup passes it to
`container create --memory`, and the Compose fallback maps it to `mem_limit`. It is independent
of both file size and pixel count: increasing memory does not remove the
pixel-safety check, while too little memory can still cause a high-resolution
processing job to be killed by the container.

Do not put the bot API URL in `.env`: Apple container assigns the API container
an address when it starts, and the script injects the current address into the
bot automatically.

## Commands

```sh
./startup.sh start       # reuse a matching local image; build only if needed
./startup.sh build       # build with local cache; does not require .env
./startup.sh status
./startup.sh logs        # print both service logs
./startup.sh logs bot -f
./startup.sh logs api -f
./startup.sh stop        # stop only this project's two containers
./startup.sh update      # explicit pull/rebuild, then reconcile services
./startup.sh supervisor status
./startup.sh supervisor enable
./startup.sh supervisor disable
```

`start`/`up` compute a digest of the Dockerfile build inputs (`Dockerfile`,
`requirements.txt`, `bot.py`, `.dockerignore` when present, and `assets/`) and
store it as an image label. If the configured local image already has the same
digest, the image is reused and no `--pull`, `apt-get`, or pip install is run.
If the image is missing or its digest is stale, `start` builds with the local
base-image/build cache. The first run after this logic or the mirror change may
therefore build once. When both project containers are already running with a
matching image, `start` leaves them running.

`update` is the explicit refresh operation: it runs the image build with
`--pull --no-cache`, then stops and recreates only the two project containers.
The persistent host data directory and downloaded local Bot API data are not
removed. Images, networks, and volumes are never pruned by the script.

`build` also uses the local cache and does not pull. Use `update` when a fresh
base-image pull is intentional.

Apple `container` has no Docker Compose `depends_on` or `restart` equivalent in
the CLI used here. The script implements the dependency explicitly: API
container creation/start, API IP/port wait, then bot creation/start. If either
service later exits, the launchd supervisor retries the two project containers;
use `status` and `logs` to diagnose it.

### macOS launchd supervisor and TCC

The supervisor source and plist template remain in this repository, but the
LaunchAgent never executes a file from `Documents`. `./startup.sh supervisor
enable` atomically installs a 700-mode runtime copy at
`~/Library/Application Support/TG_watermarker/tg-watermarker-supervisor.sh`,
the state and launchd logs under its `state/` directory, and the rendered plist
at `~/Library/LaunchAgents/com.tg-watermarker.supervisor.plist`. The plist's
`ProgramArguments` and `WorkingDirectory` use only that home-directory runtime
location, and it has both `RunAtLoad` and `KeepAlive` enabled.

`./startup.sh stop` (or `supervisor disable`) writes an intentional-stop marker
before unloading the job. A later failed install/bootstrap leaves the marker
in place; only a successful `supervisor enable` removes it. If the older
repository-local marker exists, the next enable copies it to the new state
directory before bootstrapping. The supervisor only inspects and starts
`tg-watermarker-bot` and `tg-watermarker-telegram-bot-api`; it never operates
on other containers.

The API listens on container port `8081` only. No host port is published,
matching the existing Compose file; the bot reaches it over the private project
network. The persistent Telegram API data directory is mounted at
`/var/lib/telegram-bot-api`, read-write in the API and read-only in the bot.

The script deliberately uses the API's IP instead of a bare
`telegram-bot-api` hostname. Apple's current custom-network behavior does not
provide Compose-style bare-name discovery, so this avoids requiring a global
DNS-domain configuration on the Mac.

### Why the macOS data mount is a bind mount

Apple named volumes are ext4 block-device images. On this local
`container` 1.4.1 / macOS 26.6 installation, attaching the same named volume
read-write to the API and read-only to the bot failed with
`VZErrorDomain Code=2` (`The storage device attachment is invalid`). A host
directory bind mount worked concurrently for both containers, so the Apple
path uses `--volume <host-directory>:/var/lib/telegram-bot-api[:ro]` and keeps
the data under the ignored `.container-data/` directory by default. This still
provides persistent volume-style storage while avoiding the runtime limitation.
The Linux/Docker path below keeps the original named volume.

## Linux / Docker compatibility

`compose.yaml` remains as a documented fallback for Linux or an existing Docker
environment. It retains the original two-service design, restart policy, and
Compose service-name networking. It is not invoked by `startup.sh` and Docker
Desktop is not a macOS prerequisite.

For Linux/Docker:

```sh
docker compose --env-file .env up --build -d
docker compose logs -f bot
docker compose down
```

## Troubleshooting

- `Missing .env`: create it from `.env.example`; the script never invents or
  writes secrets.
- Required value placeholder error: replace all three `replace-with-...`
  values, without committing `.env`.
- Build failure: check `container system status`, `container builder status`,
  available disk/memory, and registry/network access; retry
  `./startup.sh build`. Use `./startup.sh update` only when a fresh base-image
  pull is intended. The script starts the CLI's builder automatically when it
  is not running. The first build may take a long time while it downloads the
  base image and CJK font package.
- API container stopped: run `./startup.sh status` and
  `./startup.sh logs api`. Common causes are invalid API ID/hash or insufficient
  connectivity to Telegram.
- Bot container stopped: run `./startup.sh logs bot`. Check the bot token and
  whether the local API container is still running.
- A resource name is already owned by another application: stop and resolve
  that naming conflict manually; the script does not delete unrelated
  containers, networks, volumes, or images.

## Apple container references

- [Apple container command reference](https://github.com/apple/container/blob/main/docs/command-reference.md)
- [Apple container networking](https://github.com/apple/container/blob/main/docs/networking.md)
- [Apple container volumes](https://github.com/apple/container/blob/main/docs/volumes.md)

## Debian package sources

The Dockerfile writes a deb822 APT source file for Bookworm and
Bookworm-updates using the Debian mirror-list entry
`https://debian.cs.nycu.edu.tw/debian`, plus
`https://security.debian.org/debian-security` for security updates. Both are
HTTPS sources. APT continues to verify signed `InRelease`/Release metadata with
the base image's `/usr/share/keyrings/debian-archive-keyring.gpg`; no
`--allow-unauthenticated` or third-party package source is used.
