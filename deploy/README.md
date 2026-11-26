# K3s and Argo CD deployment

The K3s overlay uses the cluster's Traefik ingress and serves the site over
plain HTTP on the node's LAN address. No public hostname, TLS certificate, or
internet ingress is configured.

## CI/CD and image publishing

Every push to `main` runs `.github/workflows/deploy.yml`. GitHub Actions builds
the root `Dockerfile` for `linux/amd64` and `linux/arm64`, then publishes both
`latest` and the source commit SHA to
`ghcr.io/htharshtripathi2012-code/chess`. It uses the repository-provided
`GITHUB_TOKEN`; no long-lived registry credential is required.

After publishing, the workflow updates the K3s overlay to the immutable SHA
tag and pushes that manifest change to `main` with `[skip ci]`. Argo CD watches
the same branch and automatically syncs the updated image. The Deployment
uses `IfNotPresent` because each manifest points to a new SHA tag.

For anonymous K3s pulls, make the `chess` package public in GitHub Packages
after its first successful publication. If the package should remain private,
configure an `imagePullSecret` in the `chess` namespace instead. Also ensure
repository Actions settings permit the workflow's `contents: write` and
`packages: write` permissions, and that branch rules allow the workflow to
push the generated deployment commit.

The Deployment intentionally has one replica: rooms and clocks are held in
memory and are lost when the pod restarts.

## Deploy with Argo CD

Push this project, including `.github/workflows/`, `deploy/k8s/`, and
`deploy/argocd/`, to the `main` branch of the configured Git repository. Then
apply the Argo CD Application once:

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
