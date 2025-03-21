import azure.functions as func
import json

app = func.FunctionApp()

@app.route(route="hello", auth_level=func.AuthLevel.ANONYMOUS)
async def hello(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"message": "fonction deployée"}),
        mimetype="application/json",
        status_code=200
    )