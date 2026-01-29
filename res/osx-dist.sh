#!/usr/bin/env bash

echo "MACOS_CODESIGN_IDENTITY: $MACOS_CODESIGN_IDENTITY"
cargo install flutter_rust_bridge_codegen --version 1.80.1 --features uuid
cd flutter; flutter pub get; cd -
~/.cargo/bin/flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/macos/Runner/bridge_generated.h
./build.py --flutter
rm -f rustdesk-$VERSION.dmg

if [ -n "$MACOS_CODESIGN_IDENTITY" ]; then
    echo "Signing with identity: $MACOS_CODESIGN_IDENTITY"
    # security find-identity -v
    codesign --force --options runtime -s $MACOS_CODESIGN_IDENTITY --deep --strict ./flutter/build/macos/Build/Products/Release/RustDesk.app -vvv
    create-dmg --icon "RustDesk.app" 200 190 --hide-extension "RustDesk.app" --window-size 800 400 --app-drop-link 600 185 rustdesk-$VERSION.dmg ./flutter/build/macos/Build/Products/Release/RustDesk.app
    codesign --force --options runtime -s $MACOS_CODESIGN_IDENTITY --deep --strict rustdesk-$VERSION.dmg -vvv
    # notarize the rustdesk-${{ env.VERSION }}.dmg
    rcodesign notary-submit --api-key-path ~/.p12/api-key.json  --staple rustdesk-$VERSION.dmg
else
    echo "No signing identity provided, creating unsigned distribution..."
    # Remove any existing signatures
    codesign --remove-signature --deep ./flutter/build/macos/Build/Products/Release/RustDesk.app 2>/dev/null || true
    # Remove extended attributes that may cause Gatekeeper issues
    xattr -cr ./flutter/build/macos/Build/Products/Release/RustDesk.app 2>/dev/null || true
    # Create unsigned DMG
    create-dmg --icon "RustDesk.app" 200 190 --hide-extension "RustDesk.app" --window-size 800 400 --app-drop-link 600 185 rustdesk-$VERSION.dmg ./flutter/build/macos/Build/Products/Release/RustDesk.app
    echo "Created unsigned DMG: rustdesk-$VERSION.dmg"
    echo "Note: Users may need to right-click -> Open or run 'xattr -cr /Applications/RustDesk.app' to bypass Gatekeeper"
fi
