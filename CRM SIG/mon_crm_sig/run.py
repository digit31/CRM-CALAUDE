import uvicorn

if __name__ == "__main__":
    # Point d'entrée pour lancer le serveur local.
    # On spécifie le module app.main:app
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
