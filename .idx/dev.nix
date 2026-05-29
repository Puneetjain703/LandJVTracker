# To learn more about how to use Nix to configure your environment
# see: https://developers.google.com/idx/guides/customize-idx-env
{ pkgs, ... }: {
  # Which git commit or channel of nixpkgs to use
  channel = "stable-24.05"; # or stable-23.11

  # Use standard packages like Python, Pip, PostgreSQL
  packages = [
    pkgs.python312Packages.python
    pkgs.python312Packages.pip
    pkgs.python312Packages.virtualenv
    pkgs.postgresql
  ];

  # Enable services like PostgreSQL inside the IDE
  services.postgres = {
    enable = true;
    enableTCPIP = false;
    package = pkgs.postgresql;
    setupDataDirs = true;
  };

  # Sets environment variables inside the workspace
  env = {
    DATABASE_URL = "postgresql://postgres@localhost:5432/postgres";
    API_BASE_URL = "http://localhost:8000";
  };

  # Search for the extensions you want on https://open-vsx.org/ and use "publisher.id"
  idx.extensions = [
    "ms-python.python"
    "ms-python.vscode-pylance"
  ];

  # Workspace lifecycle hooks
  idx.workspace = {
    # Runs when a workspace is first created
    onCreate = ''
      python -m venv .venv-runtime
      source .venv-runtime/bin/activate
      pip install --upgrade pip
      pip install -r requirements.txt
    '';
    
    # Runs when a workspace is restarted
    onStart = ''
      # Start postgres if not running and initialize tables
      source .venv-runtime/bin/activate
      PYTHONPATH=. python scripts/init_db.py || true
    '';
  };

  # Preview configurations and background runner processes
  idx.previews = {
    enable = true;
    previews = {
      # Streamlit Preview
      web = {
        command = [
          ".venv-runtime/bin/streamlit"
          "run"
          "frontend/streamlit_app.py"
          "--server.port"
          "$PORT"
          "--server.address"
          "0.0.0.0"
        ];
        manager = "web";
      };
      
      # FastAPI Backend Runner
      backend = {
        command = [
          ".venv-runtime/bin/uvicorn"
          "backend.app.main:app"
          "--host"
          "0.0.0.0"
          "--port"
          "8000"
          "--reload"
        ];
        manager = "web";
      };
    };
  };
}
