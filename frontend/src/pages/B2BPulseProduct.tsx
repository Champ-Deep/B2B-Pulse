import React from 'react';
import {
    Zap,
    Target,
    MessageSquare,
    Smartphone,
    ArrowRight,
    CheckCircle2,
    BarChart3,
    ShieldCheck,
    Globe,
    Users
} from 'lucide-react';

const B2BPulseProduct: React.FC = () => {
    return (
        <div className="min-h-screen bg-slate-50 overflow-x-hidden">
            {/* Navigation (Simplified) */}
            <nav className="fixed top-0 w-full z-50 bg-white/80 backdrop-blur-md border-b border-slate-100">
                <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
                    <div className="flex items-center space-x-2">
                        <span className="text-2xl font-extrabold tracking-tighter text-brand-navy">
                            Lake<span className="brand-text-gradient">B2B</span>
                        </span>
                        <div className="h-4 w-[1px] bg-slate-200 mx-2" />
                        <span className="text-sm font-bold tracking-widest uppercase text-slate-400">Pulse</span>
                    </div>
                    <div className="hidden md:flex items-center space-x-8">
                        <a href="#features" className="text-sm font-semibold text-slate-600 hover:text-brand-red transition">Features</a>
                        <a href="#automation" className="text-sm font-semibold text-slate-600 hover:text-brand-red transition">Automation</a>
                        <a href="#analytics" className="text-sm font-semibold text-slate-600 hover:text-brand-red transition">Analytics</a>
                        <button className="brand-gradient text-white px-6 py-2 rounded-full font-bold text-sm btn-hover-effect">
                            Launch App
                        </button>
                    </div>
                </div>
            </nav>

            {/* Hero Section */}
            <section className="pt-40 pb-24 px-6 relative">
                <div className="absolute top-0 right-0 w-1/2 h-full bg-gradient-to-l from-brand-yellow/5 to-transparent -z-10" />
                <div className="max-w-7xl mx-auto grid lg:grid-cols-2 gap-16 items-center">
                    <div>
                        <div className="inline-flex items-center space-x-2 bg-white px-3 py-1 rounded-full shadow-sm mb-6 border border-slate-100">
                            <span className="flex h-2 w-2 rounded-full bg-brand-red animate-pulse"></span>
                            <span className="text-xs font-bold uppercase tracking-wider text-slate-600">Now in Private Beta</span>
                        </div>
                        <h1 className="text-5xl lg:text-7xl font-extrabold text-brand-navy leading-[1.1] mb-8">
                            The Heartbeat of <br />
                            <span className="brand-text-gradient">B2B Social Social.</span>
                        </h1>
                        <p className="text-xl text-slate-600 leading-relaxed mb-10 max-w-xl">
                            B2B Pulse automates your brand's presence across LinkedIn and Meta. Human-like engagement, real-time WhatsApp triggers, and AI-driven voice cloning at scale.
                        </p>
                        <div className="flex flex-col sm:flex-row space-y-4 sm:space-y-0 sm:space-x-4">
                            <button className="brand-gradient text-white px-8 py-4 rounded-full font-bold shadow-xl btn-hover-effect flex items-center justify-center">
                                Start Automating
                                <ArrowRight className="ml-2 w-5 h-5" />
                            </button>
                            <button className="bg-white text-brand-navy border-2 border-slate-200 px-8 py-4 rounded-full font-bold hover:border-brand-red transition flex items-center justify-center">
                                Watch Demo
                            </button>
                        </div>
                    </div>

                    {/* Interactive Demo Preview Card */}
                    <div className="relative">
                        <div className="brand-border-gradient p-1 shadow-2xl">
                            <div className="bg-white rounded-[1.4rem] overflow-hidden">
                                <div className="bg-brand-navy p-4 flex items-center justify-between">
                                    <div className="flex space-x-2">
                                        <div className="w-3 h-3 rounded-full bg-slate-700" />
                                        <div className="w-3 h-3 rounded-full bg-slate-700" />
                                        <div className="w-3 h-3 rounded-full bg-slate-700" />
                                    </div>
                                    <span className="text-[10px] text-slate-400 font-mono">APP_VERSION: 1.0.4-STABLE</span>
                                </div>
                                <div className="p-8">
                                    <div className="flex items-center justify-between mb-8">
                                        <div className="flex items-center space-x-4">
                                            <div className="w-12 h-12 rounded-2xl brand-gradient flex items-center justify-center text-white shadow-lg">
                                                <Zap className="w-6 h-6" />
                                            </div>
                                            <div>
                                                <h4 className="font-bold text-brand-navy">Weekly Engagement</h4>
                                                <p className="text-xs text-slate-400">Real-time Pulse Monitoring</p>
                                            </div>
                                        </div>
                                        <div className="text-right">
                                            <p className="text-2xl font-black text-brand-navy">2,482</p>
                                            <p className="text-[10px] font-bold text-green-500 uppercase tracking-tighter">↑ 12.4% vs Last Week</p>
                                        </div>
                                    </div>

                                    {/* Fake Chart Lines */}
                                    <div className="space-y-4 mb-8">
                                        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                                            <div className="h-full w-[85%] brand-gradient rounded-full animate-pulse" />
                                        </div>
                                        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                                            <div className="h-full w-[60%] brand-gradient opacity-60 rounded-full" />
                                        </div>
                                        <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                                            <div className="h-full w-[40%] brand-gradient opacity-30 rounded-full" />
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-3 gap-4">
                                        <div className="bg-slate-50 p-4 rounded-xl border border-slate-100 text-center">
                                            <p className="text-[10px] uppercase font-bold text-slate-400 mb-1">LinkedIn</p>
                                            <p className="font-bold text-brand-navy">1.2k</p>
                                        </div>
                                        <div className="bg-slate-50 p-4 rounded-xl border border-slate-100 text-center">
                                            <p className="text-[10px] uppercase font-bold text-slate-400 mb-1">Meta</p>
                                            <p className="font-bold text-brand-navy">842</p>
                                        </div>
                                        <div className="bg-slate-50 p-4 rounded-xl border border-slate-100 text-center">
                                            <p className="text-[10px] uppercase font-bold text-slate-400 mb-1">WhatsApp</p>
                                            <p className="font-bold text-brand-navy">440</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Floating Element */}
                        <div className="absolute -bottom-6 -right-6 bg-white p-6 rounded-2xl shadow-2xl border border-slate-100 hidden md:block">
                            <div className="flex items-center space-x-3 mb-4">
                                <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center text-green-600">
                                    <CheckCircle2 className="w-5 h-5" />
                                </div>
                                <p className="text-sm font-bold text-brand-navy">Comment Posted</p>
                            </div>
                            <p className="text-xs text-slate-500 leading-tight italic">
                                "Excellent insights on the Q4 data trends! Looking forward to seeing how this scales..."
                            </p>
                        </div>
                    </div>
                </div>
            </section>

            {/* Feature Grid */}
            <section id="features" className="py-24 px-6 bg-white">
                <div className="max-w-7xl mx-auto">
                    <div className="text-center mb-20">
                        <h2 className="text-4xl font-extrabold text-brand-navy mb-4">Built for Revenue Orchestration.</h2>
                        <p className="text-slate-500 max-w-2xl mx-auto">Everything you need to automate your social footprint without losing the human touch.</p>
                    </div>

                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                        <FeatureCard
                            icon={<Smartphone className="w-6 h-6" />}
                            title="WhatsApp Triggers"
                            description="Drop a link in your team's WhatsApp group and watch B2B Pulse instantly engage across all platforms."
                        />
                        <FeatureCard
                            icon={<MessageSquare className="w-6 h-6" />}
                            title="AI Voice Cloning"
                            description="Our LLM engine learns your style, tone, and brand rules to generate comments that sound just like you."
                        />
                        <FeatureCard
                            icon={<Target className="w-6 h-6" />}
                            title="Hyper-Targeted Polling"
                            description="Define high-value profiles and let the system hunt for the first-comment advantage 24/7."
                        />
                        <FeatureCard
                            icon={<Users className="w-6 h-6" />}
                            title="Team Multi-Tenancy"
                            description="Manage multiple team accounts, workspaces, and brand voices from a single control plane."
                        />
                        <FeatureCard
                            icon={<ShieldCheck className="w-6 h-6" />}
                            title="Risk-Safe Pacing"
                            description="Human-like delays and randomized action cycles keep your social accounts safe and healthy."
                        />
                        <FeatureCard
                            icon={<BarChart3 className="w-6 h-6" />}
                            title="Deep Analytics"
                            description="Track reaction times, engagement coverage, and impression lift across your entire social pipeline."
                        />
                    </div>
                </div>
            </section>

            {/* Internal Tooling / Private Beta CTA */}
            <section className="py-24 px-6">
                <div className="max-w-7xl mx-auto">
                    <div className="bg-brand-navy rounded-[3rem] p-12 lg:p-20 relative overflow-hidden">
                        <div className="absolute inset-0 opacity-10" style={{ backgroundImage: 'radial-gradient(#fff 1px, transparent 1px)', backgroundSize: '30px 30px' }}></div>
                        <div className="relative z-10 grid lg:grid-cols-2 gap-16 items-center">
                            <div>
                                <h2 className="text-4xl lg:text-5xl font-extrabold text-white mb-6">Empowering the <br /><span className="text-brand-yellow">Champions Group</span> Ecosystem.</h2>
                                <p className="text-slate-300 text-lg mb-10 leading-relaxed">
                                    B2B Pulse is currently exclusive to LakeB2B and internal Champions Group teams. If you're a partner or team member looking for early access, please request a workspace invite.
                                </p>
                                <div className="flex space-x-4">
                                    <button className="bg-brand-yellow text-brand-navy px-8 py-4 rounded-full font-bold btn-hover-effect">
                                        Request Workspace
                                    </button>
                                    <button className="text-white border-2 border-white/20 px-8 py-4 rounded-full font-bold hover:bg-white/10 transition">
                                        Contact Engineering
                                    </button>
                                </div>
                            </div>
                            <div className="hidden lg:block relative">
                                <div className="bg-white/5 backdrop-blur-3xl rounded-3xl p-10 border border-white/10">
                                    <div className="space-y-6">
                                        <div className="flex items-center space-x-4">
                                            <div className="w-3 h-3 rounded-full bg-brand-yellow" />
                                            <p className="text-white font-mono text-sm leading-none pt-1">Initializing LinkedIn Driver...</p>
                                        </div>
                                        <div className="flex items-center space-x-4">
                                            <div className="w-3 h-3 rounded-full bg-brand-red" />
                                            <p className="text-white font-mono text-sm leading-none pt-1">Parsing WhatsApp Group: #Growth-Drops</p>
                                        </div>
                                        <div className="flex items-center space-x-4">
                                            <div className="w-3 h-3 rounded-full bg-green-400" />
                                            <p className="text-white font-mono text-sm leading-none pt-1">Claude-Sonnet: Comment Generated (98% match)</p>
                                        </div>
                                        <div className="pt-4 border-t border-white/10">
                                            <div className="flex justify-between text-[10px] text-slate-500 font-mono">
                                                <span>LATENCY: 242ms</span>
                                                <span>STATUS: ACTIVE</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* Footer */}
            <footer className="bg-white border-t border-slate-100 py-12 px-6">
                <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center space-y-6 md:space-y-0">
                    <div className="flex items-center space-x-2">
                        <span className="text-xl font-extrabold tracking-tighter text-brand-navy">
                            Lake<span className="brand-text-gradient">B2B</span>
                        </span>
                    </div>
                    <p className="text-xs text-slate-400 font-medium">© 2026 B2B Pulse by Champions Group. All rights reserved.</p>
                    <div className="flex items-center space-x-6">
                        <a href="#" className="text-slate-400 hover:text-brand-navy transition"><Globe className="w-5 h-5" /></a>
                        <a href="#" className="text-slate-400 hover:text-brand-navy transition"><Target className="w-5 h-5" /></a>
                    </div>
                </div>
            </footer>
        </div>
    );
};

const FeatureCard: React.FC<{ icon: React.ReactNode; title: string; description: string }> = ({ icon, title, description }) => (
    <div className="p-8 rounded-3xl border border-slate-100 bg-white hover:shadow-2xl hover:scale-[1.02] transition-all duration-500 group">
        <div className="w-14 h-14 rounded-2xl bg-slate-50 text-brand-navy flex items-center justify-center mb-6 group-hover:brand-gradient group-hover:text-white group-hover:rotate-6 transition-all duration-500 shadow-sm">
            {icon}
        </div>
        <h4 className="text-xl font-bold text-brand-navy mb-3">{title}</h4>
        <p className="text-slate-500 text-sm leading-relaxed">{description}</p>
    </div>
);

export default B2BPulseProduct;
