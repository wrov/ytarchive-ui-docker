# syntax=docker/dockerfile:1

FROM python:3.10.3-slim-buster
WORKDIR /app
COPY requirements.txt requirements.txt 
RUN pip3 install -r requirements.txt 
RUN apt-get update && apt-get -y install ffmpeg && rm -rf /var/lib/apt/lists/*
RUN chmod -x run.sh
COPY . .
EXPOSE 8080
CMD [ "./run.sh" ]
