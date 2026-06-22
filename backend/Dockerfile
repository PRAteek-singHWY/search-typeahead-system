# Small, standard Python image.
FROM python:3.11-slim

WORKDIR /code

# Install dependencies first so Docker can cache this layer when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY . .

# Start the API. 0.0.0.0 so it's reachable from outside the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
