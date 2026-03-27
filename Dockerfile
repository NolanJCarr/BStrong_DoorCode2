FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# ***DEBUGGING STEP***: Add commands to print directory contents and requirements file
# This helps verify that the correct files are being used in the build.
RUN ls -l
RUN cat requirements.txt

# Install any needed packages specified in a requirements.txt
# --no-cache-dir: Disables the cache, which reduces the image size.
# -r requirements.txt: Tells pip to install from the given requirements file.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container at /app
COPY . .

# Define the command to run your app using gunicorn
# Gunicorn is a production-ready web server for Python.
# --bind 0.0.0.0:$PORT: Binds Gunicorn to all network interfaces on the port specified by the PORT env var (provided by Cloud Run).
# --workers 1: The number of worker processes. For a simple app, 1 is fine.
# --threads 8: The number of threads per worker.
# main:app: Tells Gunicorn to look for an app instance in the main.py file.
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 main:app
