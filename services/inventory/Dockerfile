FROM mirror.gcr.io/python:3.13-alpine
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . . 
RUN pip install requests
EXPOSE 3002
ENTRYPOINT ["python"]
CMD ["app.py"]