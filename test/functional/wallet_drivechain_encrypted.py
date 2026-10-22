#!/usr/bin/env python3
# Copyright (c) 2026 The Chains developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""A deposit from an encrypted wallet, while other calls read the wallet, returns.

createsidechaindeposit signs through SignAndCommit. An encrypted wallet's key manager asks the
wallet whether it is locked (cs_wallet) while it holds its own lock, so signing without cs_wallet
held deadlocked against any reader of the wallet that takes cs_wallet then the manager's lock
(IsMine: getbalances, getaddressinfo, listunspent, a block connected). The app froze so on a
deposit from its encrypted wallet. Here deposits are made while threads keep reading the wallet
and blocks keep coming; every call has a timeout, so a deadlock fails the test instead of hanging it.
"""
from decimal import Decimal
import threading

from test_framework.authproxy import AuthServiceProxy
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import assert_equal, assert_raises_rpc_error

# Regtest drivechain parameters, see CRegTestParams.
ACTIVATION_PERIOD = 20
DEPOSITS = 40
READERS = 4
# Seconds any one call may take before the wallet is taken as deadlocked.
CALL_TIMEOUT = 60


class DrivechainEncryptedWalletTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1
        self.setup_clean_chain = True
        self.extra_args = [["-fallbackfee=0.0002", "-rpcthreads=16"]]

    def skip_test_if_missing_module(self):
        self.skip_if_no_wallet()

    def proxy(self, timeout=CALL_TIMEOUT):
        return AuthServiceProxy(f"{self.nodes[0].url}/wallet/{self.default_wallet_name}", timeout=timeout)

    def run_test(self):
        node = self.nodes[0]
        wallet = node.get_wallet_rpc(self.default_wallet_name)
        self.generate(node, 101)
        node.createsidechainproposal(1, "one")
        self.generate(node, ACTIVATION_PERIOD + 1)
        assert_equal([s["slot"] for s in node.listactivesidechains()], [1])
        # Many coins: each read of the wallet does some work under its locks.
        wallet.sendmany("", {wallet.getnewaddress(): Decimal("0.5") for _ in range(50)})
        self.generate(node, 1)

        self.log.info("Encrypt the wallet; locked, a deposit is refused")
        wallet.encryptwallet("passphrase")
        assert_raises_rpc_error(-13, "walletpassphrase", wallet.createsidechaindeposit, 1, "locked", Decimal("0.01"))
        wallet.walletpassphrase("passphrase", 3600)

        self.log.info(f"Unlocked: {DEPOSITS} deposits while {READERS} threads read the wallet and blocks come")
        address = wallet.getnewaddress()
        stop = threading.Event()
        errors = []
        reads = [0] * READERS

        def read(i):
            rpc = self.proxy()
            try:
                while not stop.is_set():
                    rpc.getbalances()
                    rpc.getaddressinfo(address)
                    rpc.listunspent()
                    reads[i] += 1
            except Exception as e:
                errors.append(f"reader {i}: {e!r}")
                stop.set()

        readers = [threading.Thread(target=read, args=(i,)) for i in range(READERS)]
        for t in readers:
            t.start()
        depositor = self.proxy()
        miner = AuthServiceProxy(self.nodes[0].url, timeout=CALL_TIMEOUT)
        txids = []
        try:
            for i in range(DEPOSITS):
                if stop.is_set():
                    break
                txids.append(depositor.createsidechaindeposit(1, f"deposit{i}", Decimal("0.01"))["txid"])
                miner.generatetoaddress(1, address)
        except Exception as e:
            errors.append(f"deposit {len(txids)}: {e!r}")
        finally:
            stop.set()
            for t in readers:
                t.join(CALL_TIMEOUT + 5)
        assert not any(t.is_alive() for t in readers), "a reader of the wallet is stuck"
        assert_equal(errors, [])
        assert_equal(len(txids), DEPOSITS)
        self.log.info(f"{DEPOSITS} deposits, {sum(reads)} rounds of reads")
        # Every deposit is in a block.
        for txid in txids:
            assert wallet.gettransaction(txid)["confirmations"] >= 1

        self.log.info("Locked again: refused again; the node still answers")
        wallet.walletlock()
        assert_raises_rpc_error(-13, "walletpassphrase", wallet.createsidechaindeposit, 1, "locked", Decimal("0.01"))
        wallet.getbalances()


if __name__ == '__main__':
    DrivechainEncryptedWalletTest(__file__).main()
