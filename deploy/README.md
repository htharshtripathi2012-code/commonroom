# K3s and Argo CD deployment

The K3s overlay uses the cluster's Traefik ingress and serves the site over
plain HTTP on the node's LAN address. No public hostname, TLS certificate, or
internet ingress is configured.

## CI/CD and image publishing

Every push to `main` runs `.github/workflows/deploy.yml`. GitHub Actions builds
the root `Dockerfile` for `linux/amd64` and `linux/arm64`, then publishes the
`latest` tag to `ghcr.io/htharshtripathi2012-code/chess`. It uses the
repository-provided `GITHUB_TOKEN` with job-level `contents: read` and
`packages: write` permissions. The workflow does not write to Git or commit
changes back to `main`.

For anonymous K3s pulls, make the `chess` package public in GitHub Packages
after its first successful publication. If the package should remain private,
configure registry credentials for both the Deployment (`imagePullSecret` in
the `chess` namespace) and Image Updater (a pull-secret reference and secret in
the `argocd` namespace). Ensure repository Actions settings permit the
workflow's `packages: write` permission.

Argo CD Image Updater watches the mutable `latest` tag using its digest update
strategy. When GHCR publishes a different image digest, the updater writes the
digest to the live Argo CD Application (not Git); Argo CD then syncs the
Deployment and Kubernetes pulls the new image. This live override is
pseudo-persistent: reapplying `deploy/argocd/application.yaml` replaces it.

The Deployment intentionally has one replica: rooms and clocks are held in
memory and are lost when the pod restarts.

## Deploy with Argo CD

Push this project, including `.github/workflows/`, `deploy/k8s/`, and
`deploy/argocd/`, to the `main` branch of the configured Git repository. Install
Argo CD Image Updater v0.17.0 into the `argocd` namespace, then wait for its
resources to become ready:

```sh
kubectl apply -k deploy/argocd/image-updater
kubectl -n argocd rollout status deployment/argocd-image-updater
```

Apply the Argo CD Application once (and again only if you intend to reset its
live image override):

```sh
kubectl apply -f deploy/argocd/application.yaml
```

Argo CD syncs `deploy/k8s/overlays/k3s`. Once the pod is ready, open
`http://<k3s-node-lan-ip>/` from a device on the same Wi-Fi. K3s normally
installs Traefik by default. Allow inbound HTTP on the node's private LAN
interface if its firewall blocks it; do not forward this port from the
internet.

The repository is configured as
`https://github.com/htharshtripathi2012-code/chess.git` on branch `main`.
The local project directory must be committed and pushed before Argo CD can
see these manifests.
