# VK inline keyboard provider contract

VK rejects inline keyboards when the regular-keyboard-only `one_time` field is present.

The sender facade now normalizes the final provider payload before `messages.send`: if `inline` is true, `one_time` is removed. Regular bottom keyboards still keep `one_time` when needed.
