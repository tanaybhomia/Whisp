{ lib
, stdenv
, meson
, ninja
, pkg-config
, python3
, wrapGAppsHook4
, glib
, gtk4
, libadwaita
, gobject-introspection
, gettext
, desktop-file-utils
, tesseract
}:

let
  pythonEnv = python3.withPackages (ps: with ps; [
    pygobject3
    pytesseract
    pillow
  ]);
in
stdenv.mkDerivation rec {
  pname = "whisp";
  version = "0.1.0";

  src = ./..;

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    pythonEnv
    wrapGAppsHook4
    gobject-introspection
    gettext
    desktop-file-utils
  ];

  buildInputs = [
    glib
    gtk4
    libadwaita
  ];

  dontWrapGApps = true;

  preFixup = ''
    makeWrapperArgs+=(
      "''${gappsWrapperArgs[@]}"
      --prefix PATH : "${lib.makeBinPath [ tesseract ]}"
    )
  '';

  meta = {
    homepage = "https://github.com/tanaybhomia/Whisp";
    description = "A fluid, gesture-driven scratchpad designed for speed";
    license = lib.licenses.gpl3Only;
    platforms = lib.platforms.linux;
    mainProgram = "whisp";
  };
}
