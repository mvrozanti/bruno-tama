{
  description = "bruno — a dot-blob tamagotchi that lives in your tmux pane";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      forAllSystems = f:
        nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ]
          (system: f system (import nixpkgs { inherit system; }));
    in {
      packages = forAllSystems (system: pkgs: {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "bruno";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = [ pkgs.python3Packages.pyte ];
          meta = with pkgs.lib; {
            description = "a dot-blob tamagotchi that lives in your tmux pane";
            mainProgram = "bruno";
            license = licenses.mit;
            platforms = platforms.unix;
          };
        };
      });

      apps = forAllSystems (system: pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/bruno";
        };
      });

      devShells = forAllSystems (system: pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.pyte ]))
            pkgs.tmux
          ];
        };
      });
    };
}
