FROM python:3.8-slim-buster

RUN apt-get update && apt-get --no-install-recommends install -y wget ffmpeg
COPY ./requirements.txt /app/requirements.txt

WORKDIR /app
EXPOSE 8080

# spleeter model dir
ENV MODEL=5stems
ENV MODEL_PATH /model
RUN mkdir -p /model
# finishing product
RUN mkdir -p /tmp/zip
RUN mkdir -p /tmp/upload

RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

# prepare spleeter model
RUN mkdir -p /model/$MODEL \
    && wget -O /tmp/$MODEL.tar.gz https://github.com/deezer/spleeter/releases/download/v1.4.0/$MODEL.tar.gz \
    && tar -xvzf /tmp/$MODEL.tar.gz -C /model/$MODEL/ \
    && touch /model/$MODEL/.probe

COPY . /app

CMD ["gunicorn", "-b","0.0.0.0:8080", "app:app"]