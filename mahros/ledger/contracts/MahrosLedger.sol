// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title MahrosLedger
 * @notice Permissioned consortium ledger for inter-hospital transfer agreements.
 *
 * Design constraints that matter clinically and legally:
 *  - No PHI on-chain. Only a pseudonymous patient reference and a hash of the
 *    agreed terms. The plaintext agreement never leaves the origin hospital.
 *  - Append-only. There is no update or delete path, by construction.
 *  - Two-sided attestation. An agreement is only `Confirmed` once the receiving
 *    hospital independently attests to the same terms hash, so neither party can
 *    unilaterally fabricate or later deny what was agreed.
 *  - Burden counters are maintained on-chain, which is what makes the fairness
 *    metric tamper-proof rather than self-reported.
 */
contract MahrosLedger {
    enum Status { None, Proposed, Confirmed, Completed, Cancelled }

    struct AgreementRecord {
        bytes32 termsHash;
        address origin;
        address receiver;
        uint8 resource;
        bytes32 patientRef;   // pseudonymous token, never an identifier
        uint64 agreedAt;      // simulation or wall-clock minutes
        Status status;
    }

    address public immutable admin;

    mapping(address => bool) public isMember;
    mapping(address => string) public memberName;
    address[] public members;

    mapping(bytes32 => AgreementRecord) public agreements;  // requestId => record
    bytes32[] public agreementIds;

    // Tamper-proof fairness counters.
    mapping(address => uint256) public transfersAccepted;
    mapping(address => uint256) public transfersSent;
    mapping(address => uint256) public capacityShareBps; // basis points of network capacity

    event MemberAdded(address indexed member, string name, uint256 capacityShareBps);
    event AgreementProposed(bytes32 indexed requestId, address indexed origin, address indexed receiver, bytes32 termsHash);
    event AgreementConfirmed(bytes32 indexed requestId, address indexed receiver, bytes32 termsHash);
    event AgreementCompleted(bytes32 indexed requestId, uint64 careStartedAt);
    event AgreementCancelled(bytes32 indexed requestId, string reason);

    error NotMember();
    error NotAdmin();
    error AlreadyExists();
    error UnknownAgreement();
    error WrongParty();
    error TermsMismatch();
    error BadStatus();

    modifier onlyMember() {
        if (!isMember[msg.sender]) revert NotMember();
        _;
    }

    modifier onlyAdmin() {
        if (msg.sender != admin) revert NotAdmin();
        _;
    }

    constructor() {
        admin = msg.sender;
    }

    function addMember(address hospital, string calldata name, uint256 shareBps) external onlyAdmin {
        if (!isMember[hospital]) {
            isMember[hospital] = true;
            members.push(hospital);
        }
        memberName[hospital] = name;
        capacityShareBps[hospital] = shareBps;
        emit MemberAdded(hospital, name, shareBps);
    }

    /// @notice Origin hospital records the agreement it negotiated off-chain.
    function proposeAgreement(
        bytes32 requestId,
        address receiver,
        bytes32 termsHash,
        uint8 resource,
        bytes32 patientRef,
        uint64 agreedAt
    ) external onlyMember {
        if (agreements[requestId].status != Status.None) revert AlreadyExists();
        if (!isMember[receiver]) revert NotMember();

        agreements[requestId] = AgreementRecord({
            termsHash: termsHash,
            origin: msg.sender,
            receiver: receiver,
            resource: resource,
            patientRef: patientRef,
            agreedAt: agreedAt,
            status: Status.Proposed
        });
        agreementIds.push(requestId);
        emit AgreementProposed(requestId, msg.sender, receiver, termsHash);
    }

    /// @notice Receiver attests to the identical terms hash. Only now does it count.
    function confirmAgreement(bytes32 requestId, bytes32 termsHash) external onlyMember {
        AgreementRecord storage a = agreements[requestId];
        if (a.status == Status.None) revert UnknownAgreement();
        if (a.status != Status.Proposed) revert BadStatus();
        if (msg.sender != a.receiver) revert WrongParty();
        if (a.termsHash != termsHash) revert TermsMismatch();

        a.status = Status.Confirmed;
        transfersAccepted[a.receiver] += 1;
        transfersSent[a.origin] += 1;
        emit AgreementConfirmed(requestId, msg.sender, termsHash);
    }

    function completeAgreement(bytes32 requestId, uint64 careStartedAt) external onlyMember {
        AgreementRecord storage a = agreements[requestId];
        if (a.status != Status.Confirmed) revert BadStatus();
        if (msg.sender != a.receiver) revert WrongParty();
        a.status = Status.Completed;
        emit AgreementCompleted(requestId, careStartedAt);
    }

    function cancelAgreement(bytes32 requestId, string calldata reason) external onlyMember {
        AgreementRecord storage a = agreements[requestId];
        if (a.status != Status.Proposed && a.status != Status.Confirmed) revert BadStatus();
        if (msg.sender != a.origin && msg.sender != a.receiver) revert WrongParty();
        a.status = Status.Cancelled;
        emit AgreementCancelled(requestId, reason);
    }

    // -- views used by the fairness layer ---------------------------------- //

    function agreementCount() external view returns (uint256) {
        return agreementIds.length;
    }

    function memberCount() external view returns (uint256) {
        return members.length;
    }

    /// @notice Burden in basis points of total accepted transfers, for Gini off-chain.
    function burdenOf(address hospital) external view returns (uint256 accepted, uint256 sent, uint256 shareBps) {
        return (transfersAccepted[hospital], transfersSent[hospital], capacityShareBps[hospital]);
    }

    /// @notice Anyone can re-verify a claimed agreement against the chain.
    function verifyTerms(bytes32 requestId, bytes32 termsHash) external view returns (bool) {
        return agreements[requestId].termsHash == termsHash
            && agreements[requestId].status != Status.None;
    }
}
