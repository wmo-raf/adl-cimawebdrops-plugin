# Stand-in CIMA WebDrops platform for the documentation screenshot harness.
FROM python:3.12-alpine
# zoneinfo has no database of its own on alpine, and the stub reports in the
# stations' local time (see server.py, "Clocks").
RUN pip install --no-cache-dir tzdata
COPY server.py /srv/server.py
EXPOSE 80
CMD ["python", "/srv/server.py"]
