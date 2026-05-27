# Changelog

All notable changes to Batocera Overmind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release pipeline with GitHub Actions
- Automated release notes generation
- Release script for local creation
- Drone actions now provide remote restart and Kiosk mode controls; completed actions leave the queue and appear as notifications.
- Swarm Drone tiles now show the reported Batocera version instead of the internal Drone ID.
- Profile Swarm Access now provides an explicit Remove Overseer action that revokes shared swarm access immediately.
- Pending Overseer invitations can now be resent from Profile Swarm Access, rotating the invitation link and refreshing its expiry.
- Pending Overseer invitations can now be removed from Profile Swarm Access, revoking unused invitation links immediately.
- Password signup now requires a unique username, with duplicate prevention also enforced on Profile username changes.
- Heartbeat swarm responses now provide each Drone's public peer endpoint for remote Drone-to-Drone transfers and display reported public IP details.
- Overmind now periodically probes public Drone endpoints, marks reachable Drones as resolvable in swarm and metadata views, and only advertises verified public endpoints as transfer candidates.
- ROM, BIOS, artwork, system, and bulk sync jobs now queue only Drones with verified publicly resolvable peer endpoints.
