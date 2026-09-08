FROM apify/actor-python-playwright:3.14-1.61.0

USER myuser

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Pip version:" \
 && pip --version \
 && echo "Installing dependencies:" \
 && pip install -r requirements.txt \
 && echo "All installed Python packages:" \
 && pip freeze

COPY --chown=myuser:myuser . ./

# Compile both the real bot and the Apify compatibility entrypoint.
RUN python -m compileall -q main.py my_actor/

# Apify runs the package entrypoint; my_actor/main.py delegates to root main.py.
CMD ["python", "-m", "my_actor"]
