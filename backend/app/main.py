from fastapi import FastAPI

app = FastAPI(title="HCM Lite API")

@app.get("/health")
def health_check():
    return {"status": "ok"}