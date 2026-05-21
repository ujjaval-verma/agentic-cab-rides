# vhs tape for agentic-cab-rides demo GIF
# Run:  vhs scripts/demo.tape  →  produces docs/assets/demo.gif
#
# Pre-reqs:
#   - vhs installed (brew install vhs)
#   - .env symlinked to ~/.config/agentic-cab-rides/.env (`make link-env`)
#   - ANTHROPIC_API_KEY set inside that .env
#
# Journey: vague human-style intent → resolve → confirm (gate fires) →
#          cancel (gate fires again).  Demonstrates both gated tools
#          (confirm_booking, cancel_booking) inside one session.
#
# The CLIConfirmationChannel reads bare y/n from /dev/tty (not the chat
# stream), so during a gate the tape must type only "y" — any other
# text is consumed as a decline.

Output docs/assets/demo.gif

Set Shell "zsh"
Set FontSize 13
Set Width 980
Set Height 560
Set Theme "Catppuccin Mocha"
Set Padding 16
Set TypingSpeed 25ms

# Clean slate so the action log shown is from this run only.
Type "make reset && clear"
Enter
Sleep 1500ms

# Boot the REPL agent.
Type "make repl"
Enter
Sleep 3s

# Turn 1 — vague intent, no precise dropoff address.
Type "Book me a cab from 9205 Yonge St, ON to the nearest costco"
Enter
Sleep 25s

# Turn 2 — disambiguate + pick a class.  Agent will run discover →
# prepare → confirm_booking, firing the first gate.
Type "Yes, the Richmond Hill costco. UberXL please — go ahead and confirm"
Enter
Sleep 32s

# Confirm gate: bare 'y' required.
Type "y"
Enter
Sleep 10s

# Turn 3 — cancel.  Agent will call cancel_booking, firing the second gate.
Type "Cancel that booking please"
Enter
Sleep 18s

# Cancel gate: bare 'y' required.
Type "y"
Enter
Sleep 8s

Type "/quit"
Enter
Sleep 800ms
