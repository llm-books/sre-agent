FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY chaos.py ./
ENV SCENARIO_DIR=/scenarios
ENTRYPOINT ["python", "chaos.py"]
