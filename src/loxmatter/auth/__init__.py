# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Access to the interface: password, session, throttling.

Three modules, deliberately separate and deliberately without any FastAPI
reference:

- `passwords` computes hashes and checks them. Knows neither database nor HTTP.
- `sessions` creates sessions and checks them. Knows the `AuthStore`, no HTTP.
- `throttle` counts failed attempts. Knows nothing at all except the clock.

The HTTP part lives in `loxmatter.api.auth`, the guard in
`loxmatter.loxone.server`. This separation is why the logic here is
testable without an ASGI test client - and why a secret can only ever
surface at the places that actually need it.
"""
