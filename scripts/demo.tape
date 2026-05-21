# vhs tape for agentic-cab-rides demo GIF
# Run:  vhs scripts/demo.tape  →  produces docs/assets/demo.gif
#
# Pre-reqs:
#   - vhs installed (brew install vhs)
#   - .env symlinked to ~/.config/agentic-cab-rides/.env (`make link-env`)
#   - ANTHROPIC_API_KEY set inside that .env

Output docs/assets/demo.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1100
Set Height 640
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 30ms

# Clean slate so the action log shown is from this run only.
Type "make reset && clear"
Enter
Sleep 1500ms

# Boot into the REPL agent.
Type "make repl"
Enter
Sleep 3s

Type "Book me an UberX from 1 Ferry Building San Francisco to SFO, ASAP"
Enter
Sleep 22s

Type "yes"
Enter
Sleep 8s

Type "/quit"
Enter
Sleep 800ms
