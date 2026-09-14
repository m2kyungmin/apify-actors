FROM apify/actor-python:3.14

COPY --chown=myuser:myuser requirements.txt ./
RUN python --version && pip --version && pip install -r requirements.txt && pip freeze

COPY --chown=myuser:myuser . ./
RUN python -m compileall -q my_actor/

CMD ["python", "-m", "my_actor"]
