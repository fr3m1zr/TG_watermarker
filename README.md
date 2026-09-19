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
- Network access for the first image build/pull.

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

Do not put the bot API URL in `.env`: Apple container assigns the API container
an address when it starts, and the script injects the current address into the
bot automatically.

## Commands

```sh
./startup.sh start       # build/update the ARM64 image and start both services
./startup.sh build       # build only; does not require .env
./startup.sh status
./startup.sh logs        # print both service logs
./startup.sh logs bot -f
./startup.sh logs api -f
./startup.sh stop        # stop only this project's two containers
./startup.sh update      # same reconciliation flow as start
```

`start`/`update` are safe to repeat: they build first, then stop and recreate
only the two project containers. The persistent host data directory and
downloaded local Bot API data are not removed. Images, networks, and volumes
are never pruned by the script.

Apple `container` has no Docker Compose `depends_on` or `restart` equivalent in
the CLI used here. The script implements the dependency explicitly: API
container creation/start, API IP/port wait, then bot creation/start. If either
service later exits, run `./startup.sh start` again; use `status` and `logs` to
diagnose it.

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
  `./startup.sh build`. The script starts the CLI's builder automatically when
  it is not running. The first build may take a long time while it downloads
  the base image and CJK font package.
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
