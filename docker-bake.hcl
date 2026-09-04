# docker-bake.hcl -- build definition for `docker buildx bake` and for the org's
# release workflow.
#
# The PRESENCE of this file is what makes the release action build MULTI-ARCH.
# With only a Dockerfile it builds amd64-only, which then ImagePullBackOffs on
# the arm64 cloud-tier nodes (topology.sargeant.co/tier: native-cloud).
#
#   Local build + load (arm64, matches prod):  docker buildx bake app-local
#   Multi-arch build/push (CI):                docker buildx bake app
#   Pin the version:                           VERSION=1.0.0 docker buildx bake app

variable "VERSION" {
  default = "latest"
}

variable "REGISTRY" {
  default = "ghcr.io"
}

variable "IMAGE_NAME" {
  default = "calebsargeant/afas-declaraties"
}

variable "REPO_NAME" {
  default = "afas-declaraties"
}

# arm64 first: it is the architecture that actually runs in the cluster.
variable "PLATFORMS" {
  default = "linux/arm64,linux/amd64"
}

group "default" {
  targets = ["app"]
}

target "app" {
  context    = "."
  dockerfile = "Dockerfile"
  target     = "runtime"

  platforms = split(",", PLATFORMS)

  tags = [
    "${REGISTRY}/${IMAGE_NAME}:${VERSION}",
    "${REGISTRY}/${IMAGE_NAME}:latest",
  ]

  labels = {
    "org.opencontainers.image.source"      = "https://github.com/CalebSargeant/${REPO_NAME}"
    "org.opencontainers.image.repo"        = "${REPO_NAME}"
    "org.opencontainers.image.version"     = "${VERSION}"
    "org.opencontainers.image.created"     = timestamp()
    "org.opencontainers.image.licenses"    = "MIT"
    "org.opencontainers.image.description" = "Automated expense claims: classify, approve in Slack, file in the portal"
  }

  args = {
    BUILDKIT_INLINE_CACHE = "1"
  }

  cache-from = [
    "type=registry,ref=${REGISTRY}/${IMAGE_NAME}:buildcache",
  ]

  cache-to = [
    "type=inline",
  ]

  output = ["type=image,push=true"]
}

# Local dev: single-arch so buildx can --load it into the local Docker engine
# (a multi-arch manifest cannot be loaded, only pushed). arm64 on purpose --
# Chromium behaves differently per arch and prod is arm64.
target "app-local" {
  inherits   = ["app"]
  platforms  = ["linux/arm64"]
  tags       = ["${IMAGE_NAME}:dev"]
  cache-from = []
  cache-to   = []
  output     = ["type=docker"]
}

group "all" {
  targets = ["app"]
}
