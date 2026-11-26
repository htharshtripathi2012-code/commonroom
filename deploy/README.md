# K3s and Argo CD deployment

The K3s overlay runs Commonroom behind the cluster's Traefik ingress over plain
HTTP. It uses `commonroom.192.168.1.186.nip.io`, which resolves to the LAN IP
encoded in the hostname. If the K3s node's address changes, update the host in
`deploy/k8s/base/ingress.yaml`.

## CI/CD and image publishing

Every push to `main` runs `.github/workflows/deploy.yml`. GitHub Actions checks
the Python chat server and tests, then builds the root `Dockerfile` for
`linux/amd64` and `linux/arm64` and publishes `latest` to
`ghcr.io/htharshtripathi2012-code/chess`. The repository and GHCR package retain
their historical `chess` names so the existing Argo CD image updater continues
to work. The workflow uses `GITHUB_TOKEN` with job-level `contents: read` and
`packages: write` permissions and never writes back to Git.

For anonymous K3s pulls, make the GHCR package public after its first successful
publication. If it remains private, configure registry credentials for both
the Deployment and Image Updater. Ensure repository Actions settings permit
the workflow's `packages: write` permission.

Argo CD Image Updater watches the mutable `latest` tag using its digest update
strategy. When GHCR publishes a different image digest, the updater writes the
digest to the live Argo CD Application (not Git); Argo CD then syncs the
Deployment. Reapplying `deploy/argocd/application.yaml` resets that live image
override.

## Data persistence

The Deployment mounts a 1 GiB `ReadWriteOnce` PVC at `/data` and stores
`/data/commonroom.sqlite3` there. K3s's default local-path provisioner is
expected; the PVC will stay pending if the cluster has no default StorageClass.
The data survives pod restarts, but local-path storage is tied to its node.
Back up the PVC contents for disaster recovery. Keep one replica: SQLite on a
single-node volume is not a shared multi-replica database.

## Install or update

Push this project, including `.github/workflows/`, `deploy/k8s/`, and
`deploy/argocd/`, to the `main` branch of the configured repository. Install
Argo CD Image Updater v0.17.0 into the `argocd` namespace:

```sh
kubectl apply -k deploy/argocd/image-updater
kubectl -n argocd rollout status deployment/argocd-image-updater
```

Apply the Argo CD Application once (and again only if you intend to reset its
live image override):

```sh
kubectl apply -f deploy/argocd/application.yaml
```

Argo CD syncs `deploy/k8s/overlays/k3s` into the `commonroom` namespace. When the
pod is ready, open
`http://commonroom.192.168.1.186.nip.io/` from a device on the same Wi-Fi.
Allow inbound HTTP on the node's private LAN interface if its firewall blocks
it; do not forward this port from the internet.

The source repository and GHCR image keep their existing identifiers:
`https://github.com/htharshtripathi2012-code/chess.git` and
`ghcr.io/htharshtripathi2012-code/chess`. The Argo CD Application resource is
also still named `chess` to retain the configured image-updater relationship.
