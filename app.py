# app.py
"""
Interactive HotStuff Simulator (Streamlit)
Features:
 - network graph visualization using networkx + pyvis
 - step-by-step message animation
 - dynamic N, f, faulty leader selection, and attack types
 - shows QC formation, equivocation evidence, view-change and commit
Run: streamlit run app.py
"""

import streamlit as st
import time
import hashlib
import random
from collections import defaultdict
from pyvis.network import Network
import networkx as nx
import streamlit.components.v1 as components

# -----------------------
# Helper & model classes
# -----------------------

def sign(node_id, proposal_id):
    h = hashlib.sha256((node_id + "|" + proposal_id).encode()).hexdigest()
    return f"SIG({node_id}:{h[:6]})"

class Proposal:
    def __init__(self, block_id, parent_id, view, proposer):
        self.block_id = block_id
        self.parent_id = parent_id
        self.view = view
        self.proposer = proposer

    def id(self):
        return f"{self.block_id}@v{self.view}"

class Vote:
    def __init__(self, voter, proposal, signature):
        self.voter = voter
        self.proposal = proposal
        self.signature = signature

class NodeState:
    def __init__(self, nid):
        self.id = nid
        self.received_votes = defaultdict(list)
        self.evidence = set()
        self.committed = []
        self.highest_qc = None

    def record_vote(self, vote):
        pid = vote.proposal.id()
        # store vote
        self.received_votes[pid].append(vote)
        # detect equivocation: same voter signed different proposals at same view by same proposer
        for other_pid, votes in self.received_votes.items():
            if other_pid == pid: 
                continue
            for ov in votes:
                if (ov.voter == vote.voter and
                    ov.proposal.view == vote.proposal.view and
                    ov.proposal.proposer == vote.proposal.proposer and
                    ov.proposal.block_id != vote.proposal.block_id):
                    self.evidence.add((vote.voter, ov.proposal.id(), vote.proposal.id()))

    def try_form_qc(self, proposal, QUORUM):
        pid = proposal.id()
        votes = self.received_votes.get(pid, [])
        if len(votes) >= QUORUM:
            # create QC depiction (store as tuple)
            qc = (proposal, tuple(sorted(v.voter for v in votes[:QUORUM])))
            # update highest_qc
            if self.highest_qc is None or proposal.view > self.highest_qc[0].view:
                self.highest_qc = qc
            return qc
        return None

    def apply_qc_commit(self, qc):
        proposal = qc[0]
        b = proposal.block_id
        if b not in self.committed:
            self.committed.append(b)

# -----------------------
# UI & simulation logic
# -----------------------
st.set_page_config(layout="wide", page_title="HotStuff Simulator")
st.title("🔐 HotStuff BFT Interactive Simulator")

# Sidebar controls
st.sidebar.header("Simulation Controls")
N = st.sidebar.slider("Number of validators N", min_value=4, max_value=13, value=7, step=1)
max_f = (N - 1) // 3
default_f = max(1, min(2, max_f))
F = st.sidebar.slider("Fault tolerance f (max floor((N-1)/3))", min_value=0, max_value=max_f, value=default_f)
QUORUM = 2 * F + 1

# Generate node ids A, B, C, ...
def gen_node_ids(n):
    base = []
    for i in range(n):
        # single letter then suffix if many nodes
        if i < 26:
            base.append(chr(ord('A') + i))
        else:
            base.append(f"Node{i}")
    return base

NODE_IDS = gen_node_ids(N)

faulty_leader = st.sidebar.selectbox("Choose faulty leader (or honest)", ["None"] + NODE_IDS, index=1 if N>=1 else 0)
attack_type = st.sidebar.selectbox("Attack type", ["Equivocation (split proposals)", "Withhold QC", "Drop messages (selective silence)"])
auto_run = st.sidebar.checkbox("Auto-play animation", value=True)
step_delay = st.sidebar.slider("Step delay (seconds)", 0.3, 2.0, 0.9)

st.sidebar.markdown("---")
st.sidebar.write("Quorum (2f+1) =", QUORUM)
st.sidebar.info("Tip: increase N to explore larger networks. Keep f ≤ floor((N-1)/3).")

# Create initial nodes
nodes = {nid: NodeState(nid) for nid in NODE_IDS}

col1, col2 = st.columns([2, 3])

with col1:
    st.subheader("Network Graph")
    # draw network using networkx and pyvis
    G = nx.Graph()
    for n in NODE_IDS:
        G.add_node(n)

    # make an overlay ring + random edges for visibility
    for i in range(len(NODE_IDS)):
        a = NODE_IDS[i]
        b = NODE_IDS[(i+1) % len(NODE_IDS)]
        G.add_edge(a, b)

    # Add some cross edges
    for i in range(len(NODE_IDS)//2):
        a = NODE_IDS[i]
        b = NODE_IDS[(i+len(NODE_IDS)//2) % len(NODE_IDS)]
        G.add_edge(a, b)

    net = Network(height="450px", width="100%", bgcolor="#ffffff", font_color="black")
    net.from_nx(G)
    # highlight faulty leader in red
    for n in NODE_IDS:
        if n == faulty_leader:
            net.get_node(n)['color'] = 'red'
            net.get_node(n)['title'] = f"{n} (selected)"
        else:
            net.get_node(n)['title'] = n
    net.repulsion(node_distance=120)
    path_html = "network.html"
    net.save_graph(path_html)
    HtmlFile = open(path_html, 'r', encoding='utf-8')
    components.html(HtmlFile.read(), height=480)

with col2:
    st.subheader("Simulation Log")
    log_box = st.empty()
    # function to print to log
    def log(msg, kind="info"):
        if kind == "info":
            log_box.write(msg)
        elif kind == "success":
            log_box.success(msg)
        elif kind == "warning":
            log_box.warning(msg)
        elif kind == "error":
            log_box.error(msg)
        else:
            log_box.write(msg)

    # Step controls
    if st.button("▶ Run Simulation"):
        # Reset states
        nodes = {nid: NodeState(nid) for nid in NODE_IDS}
        leader_index = 0
        view = 1

        def step_pause():
            if auto_run:
                time.sleep(step_delay)
            else:
                st.button("Next step (manual)")

        # 1) Malicious/equivocation scenario (primary demo)
        current_leader = faulty_leader if faulty_leader != "None" else NODE_IDS[leader_index % len(NODE_IDS)]
        log(f"View {view}: leader is {current_leader}", "info")

        if attack_type.startswith("Equivocation"):
            # leader creates two conflicting proposals to two halves
            propA = Proposal("X", "GENESIS", view, current_leader)
            propB = Proposal("Y", "GENESIS", view, current_leader)
            half = len(NODE_IDS)//2
            targetsA = NODE_IDS[:half]
            targetsB = NODE_IDS[half:]
            log(f"{current_leader} (leader) sends {propA.id()} to {targetsA} and {propB.id()} to {targetsB}", "info")
            step_pause()

            votes = []
            # voters for A
            for v in targetsA:
                signature = sign(v, propA.id())
                vote = Vote(v, propA, signature)
                votes.append(vote)
                # deliver to all nodes for visibility
                for nd in nodes.values():
                    nd.record_vote(vote)
                log(f"{v} voted for {propA.id()}", "info")
            step_pause()

            # voters for B
            for v in targetsB:
                signature = sign(v, propB.id())
                vote = Vote(v, propB, signature)
                votes.append(vote)
                for nd in nodes.values():
                    nd.record_vote(vote)
                log(f"{v} voted for {propB.id()}", "info")
            step_pause()

            # Try to form QC globally
            qcs = []
            for nd in nodes.values():
                qcA = nd.try_form_qc(propA, QUORUM)
                qcB = nd.try_form_qc(propB, QUORUM)
                if qcA:
                    qcs.append(qcA)
                if qcB:
                    qcs.append(qcB)

            if not qcs:
                log("No QC formed for X or Y (neither side reached quorum). Safety preserved. 🔒", "success")
            else:
                log(f"Unexpected QC formed: {qcs}", "warning")

            # collect equivocation evidence
            evidence = set()
            for nd in nodes.values():
                for e in nd.evidence:
                    evidence.add(e)
            if evidence:
                log("Equivocation evidence detected across nodes:", "warning")
                for e in evidence:
                    log(f" - {e[0]} signed conflicting proposals: {e[1]} vs {e[2]}", "warning")
            else:
                log("No equivocation evidence found.", "info")
            step_pause()

            # View change
            view += 1
            leader_index += 1
            new_leader = NODE_IDS[leader_index % len(NODE_IDS)]
            log(f"Triggering view-change -> new view {view}, new leader {new_leader}", "info")
            step_pause()

            # New leader proposes safe block (choose child's extension of highest QC; none => new block Z)
            safe_prop = Proposal("Z", "GENESIS", view, new_leader)
            log(f"{new_leader} proposes safe block {safe_prop.id()}", "info")
            step_pause()

            # All nodes vote for Z (honest behavior)
            votes_z = []
            for nid in NODE_IDS:
                sig = sign(nid, safe_prop.id())
                v = Vote(nid, safe_prop, sig)
                votes_z.append(v)
                for nd in nodes.values():
                    nd.record_vote(v)
                log(f"{nid} voted for {safe_prop.id()}", "info")
            step_pause()

            # Form QC for Z
            qcs_z = []
            for nd in nodes.values():
                qc = nd.try_form_qc(safe_prop, QUORUM)
                if qc:
                    qcs_z.append(qc)
            if qcs_z:
                # Commit via QC
                for nd in nodes.values():
                    nd.apply_qc_commit(qcs_z[0])
                log(f"QC formed for {safe_prop.id()}. All honest nodes commit {safe_prop.block_id}. ✅", "success")
            else:
                log("No QC formed for Z (unexpected).", "error")

            # final state summary
            commit_summary = {nid: nodes[nid].committed for nid in nodes}
            log("Final committed blocks per node (sample):", "info")
            for nid, commits in commit_summary.items():
                log(f" {nid}: {commits}", "info")

        elif attack_type.startswith("Withhold QC"):
            # similar template: leader proposes but withholds forming QC or withholds forwarding votes
            log("Withhold QC scenario not fully implemented in UI version yet. Please try Equivocation demo.", "info")
        else:
            log("Other attack types are placeholders. Equivocation demo is the interactive part.", "info")

        st.balloons()
        log("Simulation complete.", "info")

st.markdown("---")
st.caption("This is a simplified educational simulator. It is not a production HotStuff implementation.")
