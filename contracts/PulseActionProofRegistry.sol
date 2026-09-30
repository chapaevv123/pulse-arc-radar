// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @title PulseActionProofRegistry
/// @notice Append-only anchors for sanitized Pulse Participation Proof hashes.
/// @dev Stores no action payload, funds, tokens, upgrade hooks, or external calls.
contract PulseActionProofRegistry {
    error InvalidRecorder();
    error UnauthorizedRecorder();
    error ZeroActionId();
    error ZeroProofHash();
    error ProofAlreadyRecorded();

    address public immutable authorizedRecorder;
    mapping(bytes32 actionId => bytes32 proofHash) public proofHashByAction;

    event PulseActionProofRecorded(
        bytes32 indexed actionId,
        bytes32 indexed proofHash,
        address indexed recorder,
        uint256 timestamp
    );

    constructor(address recorder) {
        if (recorder == address(0)) revert InvalidRecorder();
        authorizedRecorder = recorder;
    }

    function recordProof(bytes32 actionId, bytes32 proofHash) external {
        if (msg.sender != authorizedRecorder) revert UnauthorizedRecorder();
        if (actionId == bytes32(0)) revert ZeroActionId();
        if (proofHash == bytes32(0)) revert ZeroProofHash();
        if (proofHashByAction[actionId] != bytes32(0)) revert ProofAlreadyRecorded();

        proofHashByAction[actionId] = proofHash;
        emit PulseActionProofRecorded(actionId, proofHash, msg.sender, block.timestamp);
    }
}
