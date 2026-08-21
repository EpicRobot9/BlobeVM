# Reproducible EpicVM overlay for the pinned Moonlight Web image.
# The patcher fails closed if the upstream minified bundle changes shape.
ARG BASE_IMAGE=mrcreativ3001/moonlight-web-stream@sha256:82cf429ffea07bdb30d3f8bf14e9e97a0a7186b0864ec4250b680b3c0c302d2b

FROM ${BASE_IMAGE} AS upstream

FROM python:3.13-slim AS patcher
USER root
COPY --from=upstream /moonlight-web/static/stream.js /tmp/stream.js
COPY patch_stream.py /tmp/patch_stream.py
RUN chmod 644 /tmp/stream.js && python /tmp/patch_stream.py /tmp/stream.js

FROM ${BASE_IMAGE}
USER root
COPY --from=patcher /tmp/stream.js /moonlight-web/static/stream.js
RUN chown 999:999 /moonlight-web/static/stream.js
USER 999:999
