FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CUTPILOT_HOST=0.0.0.0 \
    CUTPILOT_PORT=8787 \
    CUTPILOT_AI_CUT_DIRECTORY=/srv/cutpilot/AI_Cut \
    CUTPILOT_DIRECTORY=/srv/cutpilot

RUN addgroup --system cutpilot && adduser --system --ingroup cutpilot cutpilot
WORKDIR /opt/cutpilot
COPY app ./app

USER cutpilot
EXPOSE 8787
CMD ["python", "-m", "app.server"]
