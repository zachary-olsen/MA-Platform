from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router

app = FastAPI(
    title="M&A Platform",
    description="Point-in-time screening and valuation of US healthcare and semiconductor "
    "companies from SEC filings. Research and learning tool, not investment advice.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
