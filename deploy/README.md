# K3s and Argo CD deployment

The K3s overlay uses the cluster's Traefik ingress and serves the site over
plain HTTP on the node's LAN address. No public hostname, TLS certificate, or
internet ingress is configured.

## Build and import the initial image

The Deployment starts with `chess:local` and `imagePullPolicy: Never`, so it
can run before a CI/CD pipeline or registry is configured. Build the image
from the project root and import it into the K3s container runtime:

```sh
docker build -t chess:local .
docker save chess:local | sudo k3s ctr images import -
```

For a multi-node cluster, import the image on every node that may run the
workload. The Deployment intentionally has one replica: rooms and clocks are
held in memory and are lost when the pod restarts.

## Deploy with Argo CD

Push this project, including `deploy/k8s/` and `deploy/argocd/`, to the `main`
branch of the configured Git repository. Then apply the Argo CD Application
once:

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

## Later registry-based delivery

When CI/CD is added, publish an image to a registry, then change the
Deployment image from `chess:local` to the published image and change
`imagePullPolicy` from `Never` to `IfNotPresent` (or `Always` for a mutable
tag). No CI workflow is included yet.
