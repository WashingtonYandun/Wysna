FROM scratch
COPY . /sdk/
ENV PATH=/sdk/bin HOME=/work TMPDIR=/tmp WYSNA_CACHE_HOME=/work/cache
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["/sdk/bin/wysna"]
