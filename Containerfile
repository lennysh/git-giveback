FROM registry.access.redhat.com/ubi9/python-312:latest
COPY --chown=1001:0 . /opt/app-root/src/git-giveback
RUN pip install --no-cache-dir /opt/app-root/src/git-giveback
ENTRYPOINT ["git-giveback"]
