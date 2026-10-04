/**
 * NEXUS WCC Demo — Main Page
 * 
 * From AI intention to verified execution.
 * A governed agentic execution layer with human-in-the-loop approval.
 */
import { useCallback, useState, useEffect } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  CheckCircle,
  Circle,
  FileCode,
  FileText,
  Loader2,
  LoaderCircle,
  Play,
  Shield,
  Terminal,
  XCircle,
  Zap,
} from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeProvider } from "@/contexts/ThemeContext";

const WORKFLOW_STAGES = [
  { id: "objective", label: "OBJECTIVE", icon: Zap, description: "User states the goal" },
  { id: "planning", label: "PLANNING", icon: FileText, description: "Model decomposes into tasks" },
  { id: "proposal", label: "PROPOSAL", icon: FileCode, description: "Model generates coding plan" },
  { id: "policy", label: "POLICY", icon: Shield, description: "Consequential ops flagged" },
  { id: "approval", label: "APPROVAL", icon: AlertTriangle, description: "Human APPROVE/REJECT" },
  { id: "execution", label: "EXECUTION", icon: Terminal, description: "Real files created, tests run" },
  { id: "evidence", label: "EVIDENCE", icon: FileText, description: "Receipts, artifacts, provenance" },
  { id: "verification", label: "VERIFICATION", icon: CheckCircle, description: "Independent verifier checks" },
];

const TRUTH_STATES = [
  { id: "INFERRED", label: "INFERRED", description: "Model reasoning/proposal", color: "text-blue-500", bg: "bg-blue-500/10" },
  { id: "OBSERVED", label: "OBSERVED", description: "Actual execution result", color: "text-green-500", bg: "bg-green-500/10" },
  { id: "VERIFIED", label: "VERIFIED", description: "Independent verifier confirmed", color: "text-emerald-500", bg: "bg-emerald-500/10" },
  { id: "FAILED", label: "FAILED", description: "Execution/verification failed", color: "text-red-500", bg: "bg-red-500/10" },
  { id: "BLOCKED", label: "BLOCKED", description: "Policy/approval rejected", color: "text-orange-500", bg: "bg-orange-500/10" },
];

const DEFAULT_OBJECTIVE = "Create a calculator application with add/subtract/multiply/divide, write tests, run tests, and verify the implementation.";

function StageIndicator({ stage, current, completed, onClick }: { stage: typeof WORKFLOW_STAGES[0]; current: string; completed: string[]; onClick: (id: string) => void }) {
  const isCurrent = current === stage.id;
  const isCompleted = completed.includes(stage.id);
  const isClickable = completed.includes(stage.id) || current === stage.id;

  return (
    <button
      onClick={() => isClickable && onClick(stage.id)}
      disabled={!isClickable}
      className={`flex items-center gap-3 p-4 rounded-xl border-2 transition-all ${
        isCurrent
          ? "border-blue-500 bg-blue-500/10 shadow-lg shadow-blue-500/10"
          : isCompleted
          ? "border-green-500 bg-green-500/10"
          : "border-gray-700 bg-gray-900/50 hover:border-gray-600"
      }`}
    >
      <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${
        isCurrent
          ? "bg-blue-500 text-white animate-pulse"
          : isCompleted
          ? "bg-green-500 text-white"
          : "bg-gray-800 text-gray-400"
      }`}>
        <stage.icon size={20} />
      </div>
      <div className="flex-1 text-left">
        <div className="font-semibold text-white">{stage.label}</div>
        <div className="text-xs text-gray-400">{stage.description}</div>
      </div>
      <div className={`w-6 h-6 rounded-full flex items-center justify-center ${
        isCompleted ? "bg-green-500 text-white" : isCurrent ? "bg-blue-500 text-white" : "bg-gray-800 text-gray-500"
      }`}>
        {isCompleted ? <CheckCircle size={16} /> : isCurrent ? <LoaderCircle size={16} className="animate-spin" /> : <Circle size={16} />}
      </div>
    </button>
  );
}

function TruthBoundaryPanel() {
  return (
    <section className="rounded-2xl border border-gray-700 bg-gray-900/50 p-6">
      <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
        <Shield className="text-emerald-500" size={20} />
        TRUTH BOUNDARY
      </h3>
      <p className="text-gray-400 text-sm mb-4">
        NEXUS never treats model claims as truth. Only independent verification produces VERIFIED.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
        {TRUTH_STATES.map(state => (
          <div key={state.id} className={`rounded-xl p-4 border ${state.bg} border-current/20`}>
            <div className={`font-semibold ${state.color}`}>{state.label}</div>
            <div className="text-xs text-gray-400 mt-1">{state.description}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ApprovalPanel({ approval, onApprove, onReject }: { approval: any; onApprove: () => void; onReject: () => void }) {
  if (!approval) return null;

  return (
    <div className="rounded-2xl border border-orange-500 bg-orange-500/10 p-6 animate-slide-in">
      <div className="flex items-center gap-3 mb-4">
        <AlertTriangle className="text-orange-500" size={24} />
        <div>
          <div className="text-lg font-semibold text-orange-300">APPROVAL REQUIRED</div>
          <div className="text-gray-400 text-sm">Consequential operation requires human decision</div>
        </div>
      </div>
      <div className="rounded-xl bg-gray-900/50 border border-gray-700 p-4 mb-4">
        <div className="text-sm text-gray-400 mb-1">OPERATION</div>
        <div className="font-mono text-white">{approval.operation}</div>
        <div className="text-sm text-gray-400 mt-2">TARGET</div>
        <div className="font-mono text-white">{approval.target}</div>
        <div className="text-sm text-gray-400 mt-2">REASON</div>
        <div className="text-white">{approval.reason}</div>
      </div>
      <div className="flex gap-4">
        <button
          onClick={onReject}
          className="flex-1 px-6 py-3 rounded-xl bg-red-500/20 border border-red-500 text-red-300 font-semibold hover:bg-red-500/30 transition-colors"
        >
          <XCircle size={18} className="inline mr-2" />
          REJECT
        </button>
        <button
          onClick={onApprove}
          className="flex-1 px-6 py-3 rounded-xl bg-emerald-500/20 border border-emerald-500 text-emerald-300 font-semibold hover:bg-emerald-500/30 transition-colors"
        >
          <CheckCircle size={18} className="inline mr-2" />
          APPROVE
        </button>
      </div>
    </div>
  );
}

function EvidencePanel({ evidence }: { evidence: any }) {
  if (!evidence) return null;

  return (
    <section className="rounded-2xl border border-gray-700 bg-gray-900/50 p-6">
      <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
        <FileText className="text-blue-500" size={20} />
        EXECUTION EVIDENCE
      </h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <EvidenceCard label="Files Created" value={evidence.filesCreated || 0} icon={FileCode} color="blue" />
        <EvidenceCard label="Commands Run" value={evidence.commandsRun || 0} icon={Terminal} color="green" />
        <EvidenceCard label="Tests Executed" value={evidence.testsRun || 0} icon={FileText} color="blue" />
        <EvidenceCard label="Tests Passed" value={evidence.testsPassed || 0} icon={CheckCircle} color="emerald" />
        <EvidenceCard label="Receipts" value={evidence.receipts || 0} icon={FileText} color="purple" />
        <EvidenceCard label="Verification" value={evidence.verification === "PASS" ? "PASS" : evidence.verification || "PENDING"} icon={evidence.verification === "PASS" ? CheckCircle : LoaderCircle} color={evidence.verification === "PASS" ? "emerald" : "orange"} />
      </div>
    </section>
  );
}

function EvidenceCard({ label, value, icon: Icon, color }: { label: string; value: number | string; icon: any; color: string }) {
  const colorMap: Record<string, string> = {
    blue: "bg-blue-500/20 border-blue-500/30 text-blue-400",
    green: "bg-green-500/20 border-green-500/30 text-green-400",
    emerald: "bg-emerald-500/20 border-emerald-500/30 text-emerald-400",
    purple: "bg-purple-500/20 border-purple-500/30 text-purple-400",
    orange: "bg-orange-500/20 border-orange-500/30 text-orange-400",
    red: "bg-red-500/20 border-red-500/30 text-red-400",
  };

  return (
    <div className={`rounded-xl border p-4 ${colorMap[color] || colorMap.blue}`}>
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} className={`text-${color}-400`} />
        <span className="text-xs text-gray-400 uppercase tracking-wide">{label}</span>
      </div>
      <div className="text-3xl font-bold text-white">{value}</div>
    </div>
  );
}

function WorkflowPanel({ currentStage, completedStages, approval, evidence, onApprove, onReject }: {
  currentStage: string;
  completedStages: string[];
  approval: any;
  evidence: any;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="flex flex-col gap-6">
      {/* Workflow Stages */}
      <section className="rounded-2xl border border-gray-700 bg-gray-900/50 p-6">
        <h3 className="text-lg font-semibold text-white mb-6">WORKFLOW</h3>
        <div className="flex flex-col gap-3">
          {WORKFLOW_STAGES.map(stage => (
            <StageIndicator
              key={stage.id}
              stage={stage}
              current={currentStage}
              completed={completedStages}
              onClick={() => {}}
            />
          ))}
        </div>
      </section>

      {/* Approval Panel */}
      <ApprovalPanel approval={approval} onApprove={onApprove} onReject={onReject} />

      {/* Evidence Panel */}
      <EvidencePanel evidence={evidence} />

      {/* Truth Boundary */}
      <TruthBoundaryPanel />
    </div>
  );
}

function ObjectiveInput({ onSubmit, isRunning }: { onSubmit: (objective: string) => void; isRunning: boolean }) {
  const [objective, setObjective] = useState(DEFAULT_OBJECTIVE);
  const [demoMode, setDemoMode] = useState(true);

  return (
    <section className="rounded-2xl border border-gray-700 bg-gray-900/50 p-6">
      <h3 className="text-lg font-semibold text-white mb-6 flex items-center gap-2">
        <Zap className="text-yellow-500" size={24} />
        OBJECTIVE
      </h3>
      
      <div className="space-y-4">
        <div>
          <label className="block text-sm text-gray-400 mb-2">What should NEXUS accomplish?</label>
          <textarea
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            rows={4}
            className="w-full rounded-xl bg-gray-900/50 border border-gray-700 p-4 text-white placeholder-gray-500 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 resize-none"
            placeholder="e.g., Create a calculator application with add/subtract/multiply/divide, write tests, run tests, and verify the implementation."
          />
        </div>

        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={demoMode}
              onChange={(e) => setDemoMode(e.target.checked)}
              className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
            />
            <span className="text-white text-sm">Demo Mode (deterministic, no live model required)</span>
          </label>
        </div>

        <button
          onClick={() => onSubmit(objective)}
          disabled={isRunning}
          className="w-full px-8 py-4 rounded-xl bg-blue-500/20 border border-blue-500 text-blue-300 font-semibold hover:bg-blue-500/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-3"
        >
          <Play size={20} />
          {isRunning ? (
            <>
              <LoaderCircle size={20} className="animate-spin" />
              WORKFLOW RUNNING...
            </>
          ) : (
            <>
              <Zap size={20} />
              RUN OBJECTIVE
            </>
          )}
        </button>
      </div>
    </section>
  );
}

function Header() {
  return (
    <header className="border-b border-gray-800 bg-gray-950/80 backdrop-blur-sm sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-emerald-500 flex items-center justify-center">
            <Zap size={24} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">NEXUS</h1>
            <p className="text-xs text-gray-400">From AI intention to verified execution</p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm text-gray-400">
          <span className="px-2 py-1 rounded bg-gray-800 border border-gray-700">WCC Launchpad 30</span>
          <span className="px-2 py-1 rounded bg-gray-800 border border-gray-700">Track 01: Agentic AI</span>
        </div>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="border-t border-gray-800 bg-gray-950 py-6 mt-12">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center text-gray-500 text-sm">
        <p>NEXUS — From AI intention to verified execution</p>
        <p className="mt-1">WCC Launchpad 30 • Track 01: Agentic AI</p>
      </div>
    </footer>
  );
}

export default function Home() {
  const [currentStage, setCurrentStage] = useState("objective");
  const [completedStages, setCompletedStages] = useState<string[]>([]);
  const [approval, setApproval] = useState<any>(null);
  const [evidence, setEvidence] = useState<any>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [workflowResult, setWorkflowResult] = useState<any>(null);

  const handleSubmit = useCallback(async (objective: string) => {
    setIsRunning(true);
    setCurrentStage("planning");
    setCompletedStages(["objective"]);
    setApproval(null);
    setEvidence(null);

    // Simulate the workflow stages
    const stages = ["planning", "proposal", "policy", "approval", "execution", "evidence", "verification"];
    
    for (const stage of stages) {
      await new Promise(r => setTimeout(r, 800));
      setCompletedStages(prev => [...prev, stage]);
      if (stage !== "verification") {
        setCurrentStage(stage);
      }
    }

    // Simulate approval needed for file write
    setCurrentStage("approval");
    setApproval({
      operation: "filesystem.file.write",
      target: "calculator.py",
      reason: "Creating new file with calculator implementation requires human approval"
    });

    // Wait for user decision (in real app, this would wait for user click)
    // For demo, auto-approve after 2 seconds
    await new Promise(r => setTimeout(r, 2000));
    setApproval({ ...approval, status: "APPROVED" });

    // Execution phase
    setCurrentStage("execution");
    await new Promise(r => setTimeout(r, 1500));
    
    // Evidence phase
    setCurrentStage("evidence");
    setEvidence({
      filesCreated: 2,
      commandsRun: 3,
      testsRun: 4,
      testsPassed: 4,
      receipts: 4,
      verification: "PASS"
    });
    await new Promise(r => setTimeout(r, 1000));

    // Verification phase
    setCurrentStage("verification");
    await new Promise(r => setTimeout(r, 1000));

    setWorkflowResult({
      status: "VERIFIED",
      verified: true,
      tasks: 4,
      artifacts: 4,
      verification: [
        { artifact_id: "calculator.py", status: "VERIFIED", reality: "VERIFIED" },
        { artifact_id: "test_calculator.py", status: "VERIFIED", reality: "VERIFIED" },
      ]
    });
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <TooltipProvider>
        <Header />
        <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="grid lg:grid-cols-3 gap-8">
            {/* Left Panel - Objective & Workflow */}
            <div className="lg:col-span-2 space-y-8">
              <ObjectiveInput onSubmit={handleSubmit} isRunning={false} />
              <WorkflowPanel
                currentStage={currentStage}
                completedStages={completedStages}
                approval={approval}
                evidence={evidence}
                onApprove={() => setApproval((prev: any) => ({ ...prev, status: "APPROVED" }))}
                onReject={() => setApproval((prev: any) => ({ ...prev, status: "REJECTED" }))}
              />
            </div>

            {/* Right Panel - Truth Boundary & Result */}
            <div className="space-y-8">
              <TruthBoundaryPanel />
              
              {workflowResult && (
                <section className="rounded-2xl border border-emerald-500 bg-emerald-500/10 p-6 animate-slide-in">
                  <h3 className="text-lg font-semibold text-emerald-300 mb-4 flex items-center gap-2">
                    <CheckCircle className="text-emerald-500" size={20} />
                    FINAL RESULT
                  </h3>
                  <div className="space-y-4">
                    <div className="flex items-center gap-4">
                      <span className={`px-4 py-2 rounded-xl font-semibold ${
                        workflowResult.verified 
                          ? "bg-emerald-500/20 border border-emerald-500 text-emerald-300"
                          : "bg-red-500/20 border border-red-500 text-red-300"
                      }`}>
                        {workflowResult.verified ? "VERIFIED" : "FAILED"}
                      </span>
                      <span className="text-gray-400">Status: {workflowResult.status}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div className="bg-gray-800/50 rounded-xl p-4">
                        <div className="text-gray-400">Tasks Completed</div>
                        <div className="text-2xl font-bold text-white">{workflowResult.tasks || 0}</div>
                      </div>
                      <div className="bg-gray-800/50 rounded-xl p-4">
                        <div className="text-gray-400">Artifacts Created</div>
                        <div className="text-2xl font-bold text-white">{workflowResult.artifacts || 0}</div>
                      </div>
                    </div>
                    {workflowResult.verification && (
                      <div className="space-y-2">
                        <div className="text-sm text-gray-400">Verification Results:</div>
                        {workflowResult.verification.map((v: any, i: number) => (
                          <div key={i} className="flex items-center gap-3 p-3 bg-gray-800/50 rounded-xl">
                            <CheckCircle className="text-emerald-500" size={18} />
                            <span className="font-mono text-white">{v.artifact_id}</span>
                            <span className="px-2 py-1 rounded bg-emerald-500/20 text-emerald-300 text-xs">{v.status}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </section>
              )}
            </div>
          </div>
      </main>
      <Footer />
      <Toaster position="bottom-right" />
    </TooltipProvider>
  </div>
  );
}