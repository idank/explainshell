import os

from explainshell.web import create_app


def main() -> None:
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST_IP", "127.0.0.1")

    app.run(debug=app.config["DEBUG"], host=host, port=port)


if __name__ == "__main__":
    main()
