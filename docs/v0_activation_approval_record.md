# V0 activation approval record

This record preserves owner approvals without changing the contract bytes bound by the approved
Activation Manifest Packet.

## Checkpoint 1 — contract

- Decision: approved
- Date: 2026-08-29
- Object: `V0 Activation and Publication Contract — Draft A`
- Contract physical SHA-256:
  `11865c1b7fc7756a16d8d9b4afd0334808eadb2330700032ed51dc80db277dfa`

## Checkpoint 2A — superseded Activation Manifest Packet

- Decision: approved by the project owner through the instruction immediately following the exact
  packet presentation: `完成完整流程，上传github`
- Date: 2026-08-30
- Packet key: `activation-manifest-packet:endoviho-rag:v0:checkpoint-2:a`
- Packet path:
  `.artifacts/v0_activation/candidates/checkpoint-2-20260829T092959Z/v0_activation_manifest_packet.json`
- Semantic SHA-256:
  `6d1eb5dc975e449d3af1c4cca10bbe4d1c96dcdb174a1b8f9d515e7a39bd0f75`
- Physical file SHA-256:
  `f7c593b76dd5ae0d7f442d620f4f362e0ec30991277edecac7f94e087fe5059d`
- Authorized scope: apply the exact candidate graph, construct and validate remaining V0 evidence,
  create and upload the V0 pull request, and prepare exact publication candidates.
- Explicitly not inferred: a human semantic-support label or signature, and final approval of a
  commit, tag, GitHub Release, or OCI digest that did not yet exist at this checkpoint.
- Lifecycle: superseded on 2026-08-30 before the candidate benchmark. The P0 runtime audit corrected
  candidate-versus-published provenance, expanded the provider client-runtime closure, and changed
  the ContextPack schema. Those corrections intentionally invalidate this packet's prompt, model,
  qualification, and human-benchmark identities. Its approval is retained as history and is not
  transferred to replacement bytes.

## Checkpoint 2B — current Activation Manifest Packet candidate

- Decision: pending explicit project-owner approval of both exact checksums below
- Date prepared: 2026-08-30
- Packet key: `activation-manifest-packet:endoviho-rag:v0:checkpoint-2:a`
- Packet path:
  `.artifacts/v0_activation/candidates/checkpoint-2-20260830T105029Z/v0_activation_manifest_packet.json`
- Semantic SHA-256:
  `a447e05a91ae8e9cd9075faae8898ca2d9da38d9171e12d7104a0d924805e470`
- Physical file SHA-256:
  `d73b06d33a1a03e1171f669327bb7566d48e9b83d1898d6a83ba4c96faf169ac`
- Provider qualification definition/report semantic SHA-256:
  `37f5fcfa59baab28296d1592cd10e82264ca2c84aac11e035ec63b55cb2c114c` /
  `5ead3008e44e2aa73c594a276e4c00509a1fe2d01633410f6d09abe59c27630f`
- Model/prompt policy semantic SHA-256:
  `43a819d8532b3b267d8426c94134f287cd01152edd6657c28a522c13a2fead94` /
  `5d456d6083d6b4101f9877327c432a61a9d9a6dfee54986ed2e0a0ef02315a2b`
- Human benchmark definition semantic SHA-256:
  `8b0b062efc04dd545bb0c2638392a712a3a91ca720f0a079b3d03f49af7c279f`
- Verification: exact canonical bytes and every referenced raw/typed artifact reproduced locally;
  independent review is pending.
- Authorized scope: none until the exact replacement Packet is approved. In particular, the prior
  Checkpoint 2A instruction does not authorize model execution against these new prompt/model bytes.

## Checkpoint 3 — human review and final publication

- Human semantic-review signature: pending
- Exact final commit/tag/release/image approval: pending
