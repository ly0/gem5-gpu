# Copyright (c) 2006-2007 The Regents of The University of Michigan
# Copyright (c) 2009 Advanced Micro Devices, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# Authors: Brad Beckmann

import math
import m5
from m5.objects import *
from m5.defines import buildEnv
from Cluster import Cluster

#
# Note: the L1 Cache latency is only used by the sequencer on fast path hits
#
class L1Cache(RubyCache):
    latency = 3

#
# Note: the L2 Cache latency is not currently used
#
class L2Cache(RubyCache):
    latency = 15

#
# Probe filter is a cache, latency is not used
#
class ProbeFilter(RubyCache):
    latency = 1

def create_system(options, system, piobus, dma_devices, ruby_system):

    if not buildEnv['GPGPU_SIM']:
        m5.util.panic("This script requires GPGPU-Sim integration to be built.")

    # Run the original protocol script
    buildEnv['PROTOCOL'] = buildEnv['PROTOCOL'][:-6]
    protocol = buildEnv['PROTOCOL']
    exec "import %s" % protocol
    try:
        (cpu_sequencers, dir_cntrls, cpu_cluster) = \
            eval("%s.create_system(options, system, piobus, dma_devices, ruby_system)" % protocol)
    except:
        print "Error: could not create system for ruby protocol inside fusion system %s" % protocol
        raise

    # If we're going to split the directories/memory controllers
    if options.num_dev_dirs > 0:
        # Add the CPU directory controllers to the cpu_cluster and update cntrl_ids
        # @TODO: Test this with DMA controllers
        for cntrl in dir_cntrls:
            cntrl.cntrl_id = len(cpu_cluster)
            cpu_cluster.add(cntrl)

    #
    # Create controller for the copy engine to connect to in CPU cluster
    # Cache is unused by controller
    #
    cache = L1Cache(size = "4096B", assoc = 2)

    cpu_ce_seq = RubySequencer(version = options.num_cpus+options.num_sc,
                               icache = cache,
                               dcache = cache,
                               access_phys_mem = True,
                               max_outstanding_requests = 64,
                               ruby_system = ruby_system)

    cpu_ce_cntrl = L1CacheCE_Controller(version = 0,
                                    cntrl_id = len(cpu_cluster),
                                    sequencer = cpu_ce_seq,
                                    number_of_TBEs = 12,
                                    ruby_system = ruby_system)

    cpu_cluster.add(cpu_ce_cntrl)

    #
    # Build GPU cluster
    #
    gpu_cluster = Cluster()

    #
    # Caches for the stream processors
    #
    l2_bits = int(math.log(options.num_l2caches, 2))
    block_size_bits = int(math.log(options.cacheline_size, 2))

    for i in xrange(options.num_sc):
        #
        # First create the Ruby objects associated with this cpu
        #
        cache = L1Cache(size = options.sc_l1_size,
                            assoc = options.sc_l1_assoc,
                            replacement_policy = "LRU",
                            start_index_bit = block_size_bits)

        l1_cntrl = L1CacheVI_Controller(version = i,
                                      cntrl_id = len(cpu_cluster)+len(gpu_cluster),
                                      cacheMemory = cache,
                                      l2_select_num_bits = l2_bits,
                                      num_l2 = options.num_l2caches,
                                      issue_latency = 30,
                                      ruby_system = ruby_system)

        cpu_seq = RubySequencer(version = options.num_cpus + i,
                                icache = cache,
                                dcache = cache,
                                access_phys_mem = True,
                                max_outstanding_requests = options.gpu_l1_buf_depth,
                                ruby_system = ruby_system)

        l1_cntrl.sequencer = cpu_seq

        exec("system.l1_cntrl_sp%02d = l1_cntrl" % i)

        #
        # Add controllers and sequencers to the appropriate lists
        #
        cpu_sequencers.append(cpu_seq)
        gpu_cluster.add(l1_cntrl)

    l2_index_start = block_size_bits + l2_bits

    for i in xrange(options.num_l2caches):
        #
        # First create the Ruby objects associated with this cpu
        #
        l2_cache = L2Cache(size = options.sc_l2_size,
                           assoc = options.sc_l2_assoc,
                           start_index_bit = l2_index_start,
                           replacement_policy = "LRU")

        l2_cntrl = L2Cache_Controller(version = i,
                                      cntrl_id = len(cpu_cluster)+len(gpu_cluster),
                                      L2cacheMemory = l2_cache,
                                      ruby_system = ruby_system)

        exec("system.l2_cntrl%d = l2_cntrl" % i)
        gpu_cluster.add(l2_cntrl)

    gpu_phys_mem_size = long(system.gpu_physmem.range.second) - \
        long(system.gpu_physmem.range.first) + 1
    
    if options.num_dev_dirs > 0:
        mem_module_size = gpu_phys_mem_size / options.num_dev_dirs
    
        #
        # determine size and index bits for probe filter
        # By default, the probe filter size is configured to be twice the
        # size of the L2 cache.
        #
        pf_size = MemorySize(options.sc_l2_size)
        pf_size.value = pf_size.value * 2
        dir_bits = int(math.log(options.num_dev_dirs, 2))
        pf_bits = int(math.log(pf_size.value, 2))
        if options.numa_high_bit:
            if options.numa_high_bit > 0:
                # if numa high bit explicitly set, make sure it does not overlap
                # with the probe filter index
                assert(options.numa_high_bit - dir_bits > pf_bits)
    
            # set the probe filter start bit to just above the block offset
            pf_start_bit = 6
        else:
            if dir_bits > 0:
                pf_start_bit = dir_bits + 5
            else:
                pf_start_bit = 6

        num_cpu_dirs = len(dir_cntrls)
        for i in xrange(options.num_dev_dirs):
            #
            # Create the Ruby objects associated with the directory controller
            #
    
            dir_version = i + num_cpu_dirs
    
            mem_cntrl = RubyMemoryControl(version = dir_version,
                                          bank_queue_size = 24,
                                          ruby_system = ruby_system)
    
            dir_size = MemorySize('0B')
            dir_size.value = mem_module_size
    
            pf = ProbeFilter(size = pf_size, assoc = 4,
                             start_index_bit = pf_start_bit)
    
            dev_dir_cntrl = Directory_Controller(version = dir_version,
                                             cntrl_id = len(cpu_cluster)+len(gpu_cluster),
                                             directory = \
                                             RubyDirectoryMemory( \
                                                        version = dir_version,
                                                        size = dir_size,
                                                        use_map = options.use_map,
                                                        map_levels = \
                                                        options.map_levels,
                                                        numa_high_bit = \
                                                        options.numa_high_bit,
                                                        device_directory = True),
                                             probeFilter = pf,
                                             memBuffer = mem_cntrl,
                                             probe_filter_enabled = options.pf_on,
                                             full_bit_dir_enabled = options.dir_on,
                                             ruby_system = ruby_system)
    
            if options.recycle_latency:
                dev_dir_cntrl.recycle_latency = options.recycle_latency
    
            exec("system.dev_dir_cntrl%d = dev_dir_cntrl" % i)
            dir_cntrls.append(dev_dir_cntrl)
            gpu_cluster.add(dev_dir_cntrl)
    else:
        # Since there are no device directories, use CPU directories
        # Fix up the memory sizes of the CPU directories
        num_dirs = len(dir_cntrls)
        add_gpu_mem = gpu_phys_mem_size / num_dirs
        for cntrl in dir_cntrls:
            new_size = cntrl.directory.size.value + add_gpu_mem
            cntrl.directory.size.value = new_size

    #
    # Create controller for the copy engine to connect to in GPU cluster
    # Cache is unused by controller
    #
    cache = L1Cache(size = "4096B", assoc = 2)

    gpu_ce_seq = RubySequencer(version = options.num_cpus+options.num_sc+1,
                               icache = cache,
                               dcache = cache,
                               access_phys_mem = True,
                               max_outstanding_requests=10, # This directly corresponds to the CE bandwidth
                               ruby_system = ruby_system)

    gpu_ce_cntrl = L1CacheCE_Controller(version = 1,
                                    cntrl_id = len(cpu_cluster)+len(gpu_cluster),
                                    sequencer = gpu_ce_seq,
                                    number_of_TBEs = 12,
                                    ruby_system = ruby_system)

    gpu_cluster.add(gpu_ce_cntrl)

    cpu_sequencers.append(cpu_ce_seq)
    cpu_sequencers.append(gpu_ce_seq)

    main_cluster = Cluster()
    main_cluster.add(cpu_cluster)
    main_cluster.add(gpu_cluster)
    if options.num_dev_dirs == 0:
        for cntrl in dir_cntrls:
            cntrl.cntrl_id = len(main_cluster)
            main_cluster.add(cntrl)

    return (cpu_sequencers, dir_cntrls, main_cluster)