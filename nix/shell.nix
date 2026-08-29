{ pkgs ? import <nixpkgs> {} }:

let
  whisp = pkgs.callPackage ./default.nix {};
in
pkgs.mkShell {
  inputsFrom = [ whisp ];

  packages = [
    (pkgs.python3.withPackages (ps: with ps; [
      pygobject3
      pytesseract
      pillow
    ]))
    pkgs.tesseract
  ];

  shellHook = ''
    export PYTHONPATH="src:$PYTHONPATH"
  '';
}
