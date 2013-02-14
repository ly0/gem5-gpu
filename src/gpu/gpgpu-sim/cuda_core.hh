/*
 * Copyright (c) 2011 Mark D. Hill and David A. Wood
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef __GPGPU_SHADER_CORE_HH__
#define __GPGPU_SHADER_CORE_HH__

#include <map>
#include <queue>
#include <set>

#include "arch/types.hh"
#include "config/the_isa.hh"
#include "cpu/translation.hh"
#include "cuda-sim/ptx_ir.h"
#include "cuda-sim/ptx_sim.h"
#include "gpgpu-sim/mem_fetch.h"
#include "gpgpu-sim/shader.h"
#include "gpu/shader_tlb.hh"
#include "mem/ruby/system/RubyPort.hh"
#include "mem/mem_object.hh"
#include "params/ShaderCore.hh"
#include "sim/process.hh"

class StreamProcessorArray;

/**
 *  Wrapper class for the shader cores in GPGPU-Sim
 *  A shader core is equivalent to an NVIDIA streaming multiprocessor (SM)
 *
 *  GPGPU-Sim shader *timing* memory references are routed through this class.
 *
 */
class ShaderCore : public MemObject
{
protected:
    typedef ShaderCoreParams Params;

    /**
     * Port for sending a receiving instuction memory requests
     * Required for implementing MemObject
     */
    class SCInstPort : public MasterPort
    {
        friend class ShaderCore;

    private:
        // Pointer back to shader core for callbacks
        ShaderCore *proc;

        // Holds packets that failed to send for retry
        std::list<PacketPtr> outInstPkts;

    public:
        SCInstPort(const std::string &_name, ShaderCore *_proc)
        : MasterPort(_name, _proc), proc(_proc) {}
        // Sends a request into the gem5 memory system (Ruby)
        bool sendPkt(PacketPtr pkt);

    protected:
        virtual bool recvTimingResp(PacketPtr pkt);
        virtual void recvRetry();
        virtual Tick recvAtomic(PacketPtr pkt);
        virtual void recvFunctional(PacketPtr pkt);
    };
    /// Instantiation of above port
    SCInstPort instPort;

    /**
    * Port to send packets to the load/store queue and coalescer
    */
    class LSQPort : public MasterPort
    {
        friend class ShaderCore;

    private:
        ShaderCore *proc;
        int idx;

    public:
        LSQPort(const std::string &_name, ShaderCore *_proc, int _idx)
        : MasterPort(_name, _proc), proc(_proc), idx(_idx) {}

    protected:
        virtual bool recvTimingResp(PacketPtr pkt);
        virtual void recvRetry();
    };
    /// Instantiation of above port
    std::vector<LSQPort*> lsqPorts;

    /// Port that is blocked. If -1 then no port is blocked.
    int writebackBlocked;

    class SenderState : public Packet::SenderState {
    public:
        SenderState(warp_inst_t _inst) : inst(_inst) {}
        warp_inst_t inst;
    };

    const Params * params() const { return dynamic_cast<const Params *>(_params);	}
    const ShaderCoreParams *_params;

    MasterID masterId;

private:

    /// Called to begin a virtual memory access
    void accessVirtMem(RequestPtr req, mem_fetch *mf, BaseTLB::Mode mode);

    /// Id for this shader core, should match the id in GPGPU-Sim
    /// To convert gem5Id = cluster_num*shader_per_cluster+num_in_cluster
    int id;

    /// Number of threads in the warp, also the number of "cores" per shader/SM
    int warpSize;

    /// Stalled because a memory request called recvRetry, usually because a queue
    /// filled up
    bool stallOnICacheRetry;

    /// holds all outstanding addresses, maps from address to mf object (from gpgpu-sim)
    /// used mostly for acking GPGPU-Sim
    std::map<Addr,mem_fetch *> busyInstCacheLineAddrs;

    /// TLB's. These do NOT perform any timing right now
    ShaderTLB *itb;

    /// Point to SPA this shader core is part of
    StreamProcessorArray *spa;

    /// Pointer to GPGPU-Sim shader this shader core is a proxy for
    shader_core_ctx *shaderImpl;

    /// Returns the line of the address, a
    Addr addrToLine(Addr a);

    /// Can we issue an inst  cache request this cycle?
    int instCacheResourceAvailable(Addr a);

public:
    /// Constructor
    ShaderCore(const Params *p);

    /// Required for implementing MemObject
    virtual BaseMasterPort& getMasterPort(const std::string &if_name, PortID idx = -1);

    /// For checkpoint restore (empty unserialize)
    virtual void unserialize(Checkpoint *cp, const std::string &section);

    /// Perform initialization. Called from SPA
    void initialize();

    /// Required for translation. Calls sendPkt with physical address
    void finishTranslation(WholeTranslationState *state);

    /** This function is used by the page table walker to determine if it could
    * translate the a pending request or if the underlying request has been
    * squashed. This always returns false for the GPU as it never
    * executes any instructions speculatively.
    * @ return Is the current instruction squashed?
    */
    bool isSquashed() const { return false; }

    /**
    * This function is the main entrypoint from GPGPU-Sim
    * This function parses the instruction from GPGPU-Sim and issues the
    * memory request to the LSQ on a per-lane basis.
    * @return true if stall
    */
    bool executeMemOp(const warp_inst_t &inst);

    /**
     * GPGPU-Sim calls this function when the writeback register in its ld/st
     * unit is cleared. If there is a lsqPort blocked, it may now try again
     */
    void writebackClear();

    // Wrapper functions for GPGPU-Sim instruction cache accesses
    void icacheFetch(Addr a, mem_fetch *mf);

    // For counting statistics
    Stats::Scalar numLocalLoads;
    Stats::Scalar numLocalStores;
    Stats::Scalar numSharedLoads;
    Stats::Scalar numSharedStores;
    Stats::Scalar numParamKernelLoads;
    Stats::Scalar numParamLocalLoads;
    Stats::Scalar numParamLocalStores;
    Stats::Scalar numConstLoads;
    Stats::Scalar numTexLoads;
    Stats::Scalar numGlobalLoads;
    Stats::Scalar numGlobalStores;
    Stats::Scalar numSurfLoads;
    Stats::Scalar numGenericLoads;
    Stats::Scalar numGenericStores;
    Stats::Scalar numDataCacheRequests;
    Stats::Scalar numDataCacheRetry;
    Stats::Scalar numInstCacheRequests;
    Stats::Scalar numInstCacheRetry;
    Stats::Vector instCounts;
    void regStats();

    void record_ld(memory_space_t space);
    void record_st(memory_space_t space);
    void record_inst(int inst_type);
    std::map<unsigned, bool> shaderCTAActive;
    std::map<unsigned, std::vector<Tick> > shaderCTAActiveStats;
    void record_block_issue(unsigned hw_cta_id);
    void record_block_commit(unsigned hw_cta_id);
    void printCTAStats(std::ostream& out);
};


#endif
