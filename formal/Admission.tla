---------------------------- MODULE Admission ----------------------------
EXTENDS Naturals
CONSTANTS Runners, Slots, PersistBeforeCall

\* second_brain/daybook.py:run (owner, pc); calls.py:Ledger.__init__/call
\* (disk/local attempts and status, chosen slot, retry); model.py:CLIModel.summarize
\* (calls is a ghost count of entries, not successful responses; unauthorized
\* remembers any repeated-attempt invocation without this run's retry flag).
VARIABLES owner, pc, disk, local, chosen, retry, calls, unauthorized
vars == <<owner, pc, disk, local, chosen, retry, calls, unauthorized>>
Empty == [s \in Slots |-> [attempts |-> 0, status |-> "empty"]]

\* Fresh runtime: storage.py:read(default={}); daybook.py:run lock unowned.
Init == /\ owner = 0
        /\ pc = [p \in Runners |-> "idle"]
        /\ disk = Empty
        /\ local = [p \in Runners |-> Empty]
        /\ chosen = [p \in Runners |-> CHOOSE s \in Slots : TRUE]
        /\ retry = [p \in Runners |-> FALSE]
        /\ calls = [s \in Slots |-> 0]
        /\ unauthorized = FALSE

\* daybook.py:run flock(LOCK_EX | LOCK_NB); failed acquisition returns
\* without state changes (stuttering). Opening the fd is not acquiring it.
Acquire(p) == /\ pc[p] = "idle" /\ owner = 0
              /\ owner' = p /\ pc' = [pc EXCEPT ![p] = "read"]
              /\ UNCHANGED <<disk, local, chosen, retry, calls, unauthorized>>

\* calls.py:Ledger.__init__ reads calls.json only AFTER run acquired flock.
\* A call's key/prompt and --retry-failed are fixed before its admission.
Read(p,s,r) == /\ pc[p] = "read"
               /\ local' = [local EXCEPT ![p] = disk]
               /\ chosen' = [chosen EXCEPT ![p] = s]
               /\ retry' = [retry EXCEPT ![p] = r]
               /\ pc' = [pc EXCEPT ![p] = "check"]
               /\ UNCHANGED <<owner, disk, calls, unauthorized>>

\* calls.py:Ledger.call previous/retry guard. Cache hit or rejection consumes
\* no attempt; model the exit separately without inventing a provider call.
Eligible(p) == LET old == local[p][chosen[p]] IN
              old.status = "empty" \/
              (retry[p] /\ old.status \in {"failed", "attempted"}
                        /\ old.attempts < 2)

\* calls.py:Ledger.call builds in-memory attempted row. Splitting local update
\* from os.replace in storage.py:write exposes crashes between them.
Admit(p) == /\ pc[p] = "check" /\ Eligible(p)
            /\ local' = [local EXCEPT ![p][chosen[p]] =
                 [attempts |-> @.attempts + 1, status |-> "attempted"]]
            /\ pc' = [pc EXCEPT ![p] = "persist"]
            /\ UNCHANGED <<owner, disk, chosen, retry, calls, unauthorized>>

\* calls.py:Ledger.call write(calls.json) BEFORE summarize; storage.py:write
\* linearization is os.replace of the WHOLE budget. A crash inside write is
\* represented by before/after this step; file data flush/fsync precede it.
Persist(p) == /\ pc[p] = "persist"
              /\ disk' = IF PersistBeforeCall THEN local[p] ELSE disk
              /\ pc' = [pc EXCEPT ![p] = "invoke"]
              /\ UNCHANGED <<owner, local, chosen, retry, calls, unauthorized>>

\* calls.py:Ledger.call -> model.py:CLIModel.summarize. The external effect
\* can have happened even if the process dies before recording a response.
Invoke(p) == /\ pc[p] = "invoke"
             /\ calls' = [calls EXCEPT ![chosen[p]] = @ + 1]
             /\ unauthorized' = unauthorized \/
                  (local[p][chosen[p]].attempts > 1 /\ ~retry[p])
             /\ pc' = [pc EXCEPT ![p] = "outcome"]
             /\ UNCHANGED <<owner, disk, local, chosen, retry>>

\* calls.py:Ledger.call completion / Ledger.fail (also validate.project failure
\* in daybook.py:_model_states). Result persistence can crash before status;
\* an attempted row permits the same single explicit retry as a failed row.
Outcome(p,st) == /\ pc[p] = "outcome"
                 /\ local' = [local EXCEPT ![p][chosen[p]].status = st]
                 /\ pc' = [pc EXCEPT ![p] = "finish"]
                 /\ UNCHANGED <<owner, disk, chosen, retry, calls, unauthorized>>

\* calls.py:Ledger.call finally / Ledger.fail atomic whole-budget write.
Finish(p) == /\ pc[p] = "finish" /\ disk' = local[p]
             /\ pc' = [pc EXCEPT ![p] = "release"]
             /\ UNCHANGED <<owner, local, chosen, retry, calls, unauthorized>>

\* daybook.py:run finally os.close releases flock. Preflight failure, deadline,
\* blocked existing slot, or finishing this selected call can exit safely.
\* Other same-run calls use the same snapshot; release/reacquire before each
\* modeled call overapproximates interference, never loses serialization.
Release(p) == /\ pc[p] \in {"check", "release"}
              /\ owner' = 0 /\ pc' = [pc EXCEPT ![p] = "idle"]
              /\ local' = [local EXCEPT ![p] = Empty]
              /\ chosen' = [chosen EXCEPT ![p] = CHOOSE s \in Slots : TRUE]
              /\ retry' = [retry EXCEPT ![p] = FALSE]
              /\ UNCHANGED <<disk, calls, unauthorized>>

\* Abrupt process termination (OS closes run's fd), NOT a caught Exception:
\* no Python finally runs. storage.py:write's completed replace survives.
\* Volatile state is discarded. A restart is a later Acquire of this runner.
Crash(p) == /\ pc[p] # "idle"
            /\ owner' = 0 /\ pc' = [pc EXCEPT ![p] = "idle"]
            /\ local' = [local EXCEPT ![p] = Empty]
            /\ chosen' = [chosen EXCEPT ![p] = CHOOSE s \in Slots : TRUE]
            /\ retry' = [retry EXCEPT ![p] = FALSE]
            /\ UNCHANGED <<disk, calls, unauthorized>>

Next == \E p \in Runners :
          Acquire(p) \/ (\E s \in Slots, r \in BOOLEAN : Read(p,s,r)) \/
          Admit(p) \/ Persist(p) \/ Invoke(p) \/
          (\E st \in {"failed", "completed"} : Outcome(p,st)) \/
          Finish(p) \/ Release(p) \/ Crash(p)

\* README daily slot contract, calls.py:Ledger.call: every provider invocation
\* has a distinct durable attempt, one initial + at most one explicit retry.
AuthorizedAttempts == /\ ~unauthorized
                      /\ \A s \in Slots :
                           calls[s] <= disk[s].attempts /\ disk[s].attempts <= 2
Spec == Init /\ [][Next]_vars
=============================================================================
