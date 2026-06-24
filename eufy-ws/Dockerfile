# Build eufy-security-ws with a patched eufy-security-client.
#
# Eufy changed HB3 camera push notifications in mid-2026. The released
# eufy-security-client 3.8.0 used by eufy-security-ws 2.1.0 silently drops the
# HB3 P2P camera push payload (inner cmd 2037), so motion/person events stop
# flowing even though recordings continue and push reports as connected.
#
# The client ref below is mirrored into an nhols-owned fork from the upstream PR
# that handles cmd 2037:
# https://github.com/bropat/eufy-security-client/pull/888
#
# Mirrored refs used here:
# - eufy-security-client argus-hb3-push-2037 -> 7267a18667bc909a0a20acee6c28b16cc808a2fb
# - eufy-security-ws     argus-ws-2.1.0      -> 01979d9d833b93bfbc9b88fbdc200745c72d6e52

FROM node:20-alpine AS build

RUN apk add --no-cache git python3 make g++

WORKDIR /build

ARG EUFY_SECURITY_CLIENT_REPO=https://github.com/nhols/eufy-security-client.git
ARG EUFY_SECURITY_CLIENT_REF=argus-hb3-push-2037
ARG EUFY_SECURITY_WS_REPO=https://github.com/nhols/eufy-security-ws.git
ARG EUFY_SECURITY_WS_REF=argus-ws-2.1.0

# 1. Clone & build patched eufy-security-client
RUN git clone --depth 1 -b "${EUFY_SECURITY_CLIENT_REF}" "${EUFY_SECURITY_CLIENT_REPO}" eufy-security-client
WORKDIR /build/eufy-security-client
RUN npm ci && npm run build && npm pack

# 2. Clone & build eufy-security-ws with the patched client tarball
WORKDIR /build
RUN git clone --depth 1 -b "${EUFY_SECURITY_WS_REF}" "${EUFY_SECURITY_WS_REPO}" eufy-security-ws
WORKDIR /build/eufy-security-ws
RUN npm ci \
    && npm uninstall eufy-security-client \
    && npm i /build/eufy-security-client/eufy-security-client-*.tgz --force \
    && npm run build

# ── runtime ──────────────────────────────────────────────────────────

FROM node:20-alpine

RUN apk add --no-cache tini bash jq

WORKDIR /usr/src/app
COPY --from=build /build/eufy-security-ws /usr/src/app

EXPOSE 3000
VOLUME /data

ENTRYPOINT ["tini", "--"]
CMD ["/bin/bash", "/usr/src/app/docker/run.sh"]
