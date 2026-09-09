# ADL CIMA WebDrops Plugin

Collects observation data from a **CIMA WebDrops** platform into an
[ADL](https://github.com/wmo-raf/adl) instance. WebDrops models a network as
sensors rather than stations: the plugin groups sensors into stations by their
coordinates, and each variable mapping names one sensor of one class. On each
collection cycle ADL authenticates with a password grant, reads each mapped
sensor's readings over the run's window, and stores them against your ADL
stations and data parameters.

**Operator guide:** [docs/guide.md](docs/guide.md) — prerequisites,
installation, every connection and station-link field, the station and sensor
selects, collection behaviour, diagnostics and troubleshooting. The guide is
also published on the central ADL documentation site.

## Development setup

The plugin runs inside the ADL core image. Build the `adl:latest` image from
the [ADL core repository](https://github.com/wmo-raf/adl) first, then:

```bash
git clone https://github.com/wmo-raf/adl-cimawebdrops-plugin.git
cd adl-cimawebdrops-plugin
cp .env.sample .env        # set PLUGIN_BUILD_UID=$(id -u), PLUGIN_BUILD_GID=$(id -g), ADL_DB_PASSWORD
docker compose build
docker compose up
docker compose exec adl adl createsuperuser
```

The admin is served on `PORT` (default 8080). The plugin source is
bind-mounted, so code changes reload the dev server. If the build fails with
`pull access denied` for `adl:latest`, prefix the build with
`DOCKER_BUILDKIT=0`.

Lint and format from `plugins/adl_cimawebdrops_plugin/` with `make lint` and
`make format`. See [CONTRIBUTING.md](CONTRIBUTING.md) — a change to any
connection or station-link field must update the guide in the same PR.
