{
  description = "Home Assistant conversation agent backed by a Hermes profile";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    nixpkgs,
    flake-utils,
    ...
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python314;
    in {
      devShells.default = pkgs.mkShell {
        packages = [
          python
          pkgs.uv
          pkgs.ruff
          pkgs.mypy
          pkgs.git
        ];

        # Home Assistant releases monthly and pins its dependency closure
        # tightly; uv resolves it from pyproject.toml rather than nixpkgs.
        # Pointing uv at the Nix interpreter stops it downloading its own.
        env = {
          UV_PYTHON = "${python}/bin/python";
          UV_PYTHON_DOWNLOADS = "never";
        };

        shellHook = ''
          echo "hermes-conversation — python $(${python}/bin/python --version | cut -d' ' -f2)"
          echo "  uv sync           install dev dependencies"
          echo "  uv run pytest     run tests"
        '';
      };

      formatter = pkgs.alejandra;
    });
}
