
"""
fg.py: fg stands for FunctionGraph
Contains the FunctionGraph class and exception
types that it can raise
"""
import sys
import graph
import utils
import toolbox
from python25 import all
from theano import config


class InconsistencyError(Exception):
    """
    This exception should be thrown by listeners to FunctionGraph when the
    graph's state is invalid.
    """
    pass

class MissingInputError(Exception):
    """
    A symbolic input needed to compute the outputs is missing.
    """
    pass



class FunctionGraph(utils.object2):
    """ WRITEME
    A FunctionGraph represents a subgraph bound by a set of input variables and a
    set of output variables, ie a subgraph that specifies a theano function.
    The inputs list should contain all the inputs
    on which the outputs depend. Variables of type Constant are
    not counted as inputs.

    The FunctionGraph supports the replace operation which allows to replace a
    variable in the subgraph by another, e.g. replace (x + x).out by (2
    * x).out. This is the basis for optimization in theano.

    This class is also reponsible for verifying that a graph is valid
    (ie, all the dtypes and broadcast patterns are compatible with the
    way the the Variables are used) and for annotating the Variables with
    a .clients field that specifies which Apply nodes use the variable.
    The .clients field combined with the .owner field and the Apply nodes'
    .inputs field allows the graph to be traversed in both directions.

    It can also be "extended" using function_graph.extend(some_object). See the
    toolbox and ext modules for common extensions.

    Features added with the`extend` function can handle the following events:

    - feature.on_attach(function_graph)
        Called by extend. The feature has great freedom in what
        it can do with the function_graph: it may, for example, add methods
        to it dynamically.

    - feature.on_detach(function_graph)
        Called by remove_feature(feature).  Should remove any dynamically-added
        functionality that it installed into the function_graph.

    - feature.on_import(function_graph, node)*
        Called whenever a node is imported into function_graph, which is
        just before the node is actually connected to the graph.

    - feature.on_prune(function_graph, node)*
        Called whenever a node is pruned (removed) from the function_graph,
        after it is disconnected from the graph.

    - feature.on_change_input(function_graph, node, i, r, new_r, [reason=None])*
        Called whenever node.inputs[i] is changed from r to new_r.
        At the moment the callback is done, the change has already
        taken place.

    - feature.orderings(function_graph)
        Called by toposort. It should return a dictionary of
        {node: predecessors} where predecessors is a list of
        nodes that should be computed before the key node.

        * If you raise an exception in the functions marked with an
          asterisk, the state of the graph might be inconsistent.

    - feature.on_setup_node(function_graph, node):
        WRITEME

    - feature.on_setup_variable(function_graph, variable):
        WRITEME

    Historically, the FunctionGraph was called an Env. Keep this in mind
    while reading out-of-date documentation, e-mail support threads, etc.

    """

    ### Special ###
    # TODO: document which things that features can do to the fgraph

    def __init__(self, inputs, outputs, features=None):
        """
        Create an FunctionGraph which operates on the subgraph bound by the inputs and
        outputs sets.

        This class keeps a pointer to the inputs and outputs, and also modifies
        them.

        #TODO: document what variables are[not] set in the FunctionGraph when a feature
        is added via the constructor.  How constructed is the FunctionGraph?

        """

        if features is None:
            features = []

        # XXX: Unless I'm missing something (but there's no documentation,
        # so I probably am) this should be a set.
        self._features = []

        # All nodes in the subgraph defined by inputs and outputs are cached in nodes
        self.nodes = set()

        # Ditto for variables
        self.variables = set()

        self.inputs = list(inputs)
        self.outputs = outputs

        for f in features:
            self.extend(f)
        self.extend(toolbox.ReplaceValidate())

        for input in self.inputs:
            if input.owner is not None:
                raise ValueError("One of the provided inputs is the output of"
                                 "an already existing node. "
                                 "If that is okay, either discard that "
                                 "input's owner or use graph.clone.")
            self.__setup_r__(input)
            self.variables.add(input)

        self.__import_r__(outputs)
        for i, output in enumerate(outputs):
            output.clients.append(('output', i))

        self.node_locks = {}
        self.variable_locks = {}
        self.profile = None


    ### Setup a Variable ###

    def __setup_r__(self, r):
        # sets up r so it belongs to this fgraph
        if hasattr(r, 'fgraph') and r.fgraph is not None and r.fgraph is not self:
            raise Exception("%s is already owned by another fgraph" % r)
        r.fgraph = self
        r.clients = []
        #self.execute_callbacks('on_setup_variable', r)

    def __setup_node__(self, node):
        # sets up node so it belongs to this fgraph
        if hasattr(node, 'fgraph') and node.fgraph is not self:
            raise Exception("%s is already owned by another fgraph" % node)
        if (hasattr(node.op, 'view_map') and
            not all([isinstance(view, (list, tuple))
                     for view in node.op.view_map.values()])):
            raise Exception("Op '%s' have a bad view map '%s',"
                            " the values must be tuples or lists." % (
                                str(node.op), str(node.op.view_map)))
        if (hasattr(node.op, 'destroy_map') and
            not all([isinstance(destroy, (list, tuple))
                     for destroy in node.op.destroy_map.values()])):
            raise Exception("Op '%s' have a bad destroy map '%s',"
                            " the values must be tuples or lists." % (
                                str(node.op), str(node.op.destroy_map)))
        node.fgraph = self
        node.deps = {}
        #self.execute_callbacks('on_setup_node', node)

    def disown(self):
        """ WRITEME
        Cleans up all of this FunctionGraph's nodes and variables so they are not
        associated with this FunctionGraph anymore.

        The FunctionGraph should not be used anymore after disown is called.

        This may not clean everything this FunctionGraph's features set in the
        nodes and variables. If there are no features, this should set
        them back to what they were originally.
        """
        for node in self.nodes:
            del node.fgraph
            del node.deps
        for variable in self.variables:
            del variable.fgraph
            del variable.clients
        self.nodes = set()
        self.variables = set()
        self.inputs = None
        self.outputs = None


    ### clients ###

    def clients(self, r):
        """
        Set of all the (node, i) pairs such that node.inputs[i] is r.
        Tell differently, a list of (node,i) such that each node have r as input at index i.
        """
        return r.clients

    def __add_clients__(self, r, new_clients):
        """ WRITEME
        r -> variable
        new_clients -> list of (node, i) pairs such that node.inputs[i] is r.

        Updates the list of clients of r with new_clients.
        """
        if set(r.clients).intersection(set(new_clients)):
            print >> sys.stderr, 'ERROR: clients intersect!'
            print >> sys.stderr, '  RCLIENTS of', r, [(n,i, type(n), id(n)) for n,i in r.clients]
            print >> sys.stderr, '  NCLIENTS of', r, [(n,i, type(n), id(n)) for n,i in new_clients]
        assert not set(r.clients).intersection(set(new_clients))
        r.clients += new_clients

    def __remove_clients__(self, r, clients_to_remove, prune = True):
        """ WRITEME
        r -> variable
        clients_to_remove -> list of (op, i) pairs such that node.inputs[i] is not r anymore.

        Removes all from the clients list of r.
        """
        for entry in clients_to_remove:
            r.clients.remove(entry)
            if entry in r.clients:
                print >> sys.stderr, 'ERROR: DUPLICATE CLIENT ENTRY...'
                print >> sys.stderr, '  ENTRY', repr(entry), type(entry[0])
                print >> sys.stderr, '  CLIENTS', repr(r.clients)
            assert entry not in r.clients # an op,i pair should be unique
        if not r.clients:
            if prune:
                self.__prune_r__([r])
                return False
            return True
        return False


    ### import ###

    def __import_r__(self, variables):
        # Imports the owners of the variables
        r_owner_done = set(self.nodes)
        for node in [r.owner for r in variables if r.owner is not None]:
            if node not in r_owner_done:
                r_owner_done.add(node)
                self.__import__(node)
        for r in variables:
            if r.owner is None and not isinstance(r, graph.Constant) and r not in self.inputs:
                raise MissingInputError("Undeclared input", r)
            if not getattr(r, 'fgraph', None) is self:
                self.__setup_r__(r)
            self.variables.add(r)

    def __import__(self, node, check = True):
        # We import the nodes in topological order. We only are interested
        # in new nodes, so we use all variables we know of as if they were the input set.
        # (the functions in the graph module only use the input set to
        # know where to stop going down)
        new_nodes = graph.io_toposort(self.variables, node.outputs)

        if check:
            for node in new_nodes:
                if hasattr(node, 'fgraph') and node.fgraph is not self:
                    raise Exception("%s is already owned by another fgraph" % node)
                for r in node.inputs:
                    if hasattr(r, 'fgraph') and r.fgraph is not self:
                        raise Exception("%s is already owned by another fgraph" % r)
                    if r.owner is None and not isinstance(r, graph.Constant) and r not in self.inputs:

                        #Verbose error message
                        #Show a complete chain of variables from the missing input to an output
                        if config.exception_verbosity == 'high':

                            def find_path_to(output_var, input_var):
                                """ Returns a list of each variable on a (not necessarily unique)
                                    path from input_var to output_var, where each variable in the
                                    list has the preceding variable as one of its inputs.
                                    Returns None if no path exists"""

                                #If output and input are the same we have a singleton path
                                if output_var is input_var:
                                    return [output_var]

                                #If output has no inputs then there is no path
                                owner = output_var.owner

                                if owner is None:
                                    return None

                                #If input_var is an input to the output node, there is a
                                #simple two element path
                                inputs = owner.inputs

                                if input_var in inputs:
                                    return [input_var, output_var]

                                #Otherwise we must recurse by searching for a path to one
                                #of our inputs, then appending the output to that path
                                for ipt in inputs:
                                    path = find_path_to(ipt, input_var)

                                    if path is not None:
                                        path.append(output_var)

                                        return path

                                #Since none of the above methods returned a path, there is none
                                return None

                            #Try different outputs until we find one that has a path to the missing input
                            for output in self.outputs:
                                path = find_path_to(output, r)

                                if path is not None:
                                    break

                            #if there is no path then r isn't really a graph input so we shouldn't be running error
                            #handler code in the first place
                            assert path is not None

                            raise MissingInputError((
                                'A variable that is an input to the graph was '
                                'neither provided as an input to the function '
                                'nor given a value. A chain of variables '
                                'leading from this input to an output is %s. '
                                'This chain may not be unique' % str(path)))

                        #Standard error message
                        raise MissingInputError((
                            "An input of the graph, used to compute %s, "
                            "was not provided and not given a value"
                            % str(node)),
                            r)

        for node in new_nodes:
            assert node not in self.nodes
            self.__setup_node__(node)
            self.nodes.add(node)
            for output in node.outputs:
                self.__setup_r__(output)
                self.variables.add(output)
            for i, input in enumerate(node.inputs):
                if input not in self.variables:
                    self.__setup_r__(input)
                    self.variables.add(input)
                self.__add_clients__(input, [(node, i)])
            assert node.fgraph is self
            self.execute_callbacks('on_import', node)


    ### prune ###

    def __prune_r__(self, variables):
        # Prunes the owners of the variables.
        for node in set(r.owner for r in variables if r.owner is not None):
            self.__prune__(node)
        for r in variables:
            if not r.clients and r in self.variables:
                self.variables.remove(r)

    def __prune__(self, node):
        if node not in self.nodes:
            raise Exception("%s does not belong to this FunctionGraph and cannot be pruned." % node)
        assert node.fgraph is self
        # If node's outputs have no clients, removes it from the graph
        # and recursively tries to prune its inputs. If at least one
        # of the op's outputs is an output to the graph or has a client
        # then __prune__ is a no-op.
        for output in node.outputs:
            # Cannot prune an op which is an output or used somewhere
            if self.clients(output) or output in self.outputs: #output in self.outputs or self.clients(output):
                return
        self.nodes.remove(node)
        self.variables.difference_update(node.outputs)
        self.execute_callbacks('on_prune', node)

        for i, input in enumerate(node.inputs):
            self.__remove_clients__(input, [(node, i)])
        #self.__prune_r__(node.inputs)



    ### change input ###

    def change_input(self, node, i, new_r, reason=None):
        """WRITEME
        Changes node.inputs[i] to new_r.

        new_r.type == old_r.type must be True, where old_r is the
        current value of node.inputs[i] which we want to replace.

        For each feature that has a 'on_change_input' method, calls:
          feature.on_change_input(function_graph, node, i, old_r, new_r, [reason])
        """
        # TODO: ERROR HANDLING FOR LISTENERS (should it complete the change or revert it?)
        if node == 'output':
            r = self.outputs[i]
            if not r.type == new_r.type:
                raise TypeError("The type of the replacement must be the"
                        " same as the type of the original Variable.",
                        r, new_r)
            self.outputs[i] = new_r
        else:
            if node.fgraph is not self:
                raise Exception("Cannot operate on %s because it does not"
                        " belong to this FunctionGraph" % node)
            r = node.inputs[i]
            if not r.type == new_r.type:
                raise TypeError("The type of the replacement must be the"
                        " same as the type of the original Variable.",
                        r, new_r)
            node.inputs[i] = new_r

        if r is new_r:
            return

        self.__import_r__([new_r])
        self.__add_clients__(new_r, [(node, i)])
        prune = self.__remove_clients__(r, [(node, i)], False)
        # Precondition: the substitution is semantically valid
        # However it may introduce cycles to the graph,  in which case the
        # transaction will be reverted later.
        self.execute_callbacks('on_change_input', node, i, r, new_r, reason=reason)

        if prune:
            self.__prune_r__([r])


    ### replace ###

    def replace(self, r, new_r, reason=None):
        """ WRITEME
        This is the main interface to manipulate the subgraph in FunctionGraph.
        For every node that uses r as input, makes it use new_r instead.
        """
        if r.fgraph is not self:
            raise Exception("Cannot replace %s because it does not belong to this FunctionGraph" % r, str(reason))
        if not r.type == new_r.type:
            raise TypeError("The type of the replacement must be the same as the type of the original Variable.", r, new_r, r.type, new_r.type, str(reason))
        if r not in self.variables:
            # this variable isn't in the graph... don't raise an exception here, just return silently
            # because it makes it easier to implement some optimizations for multiple-output ops
            return

        for node, i in list(r.clients): # copy the client list for iteration
            assert (node == 'output' and self.outputs[i] is r) or (node.inputs[i] is r)
            self.change_input(node, i, new_r, reason=reason)

        # sometimes the following is triggered.  If you understand why, please explain to James.
        # He's curious... -JB20090331
        #if len(r.clients) != 0:
        #    print >> sys.stderr, "WARNING: CLIENTS LEFT AFTER REPLACE", r, r.clients

    def replace_all(self, pairs, reason=None):
        """WRITEME"""
        for r, new_r in pairs:
            self.replace(r, new_r, reason=reason)


    ### features ###

    # XXX: This is terribly named. The "extend" method of a list
    # takes a sequence, and since this is a kind of container you
    # would expect it to do similarly.
    def extend(self, feature):
        """WRITEME
        Adds a feature to this function_graph. The feature may define one
        or more of the following methods:

        """
        if feature in self._features:
            return # the feature is already present
        attach = getattr(feature, 'on_attach', None)
        if attach is not None:
            try:
                attach(self)
            except toolbox.AlreadyThere:
                return
        self._features.append(feature)

    def remove_feature(self, feature):
        """WRITEME
        Removes the feature from the graph.

        Calls feature.on_detach(function_graph) if an on_detach method is defined.
        """
        try:
            self._features.remove(feature)
        except Exception:
            return
        detach = getattr(feature, 'on_detach', None)
        if detach is not None:
            detach(self)


    ### callback utils ###

    def execute_callbacks(self, name, *args, **kwargs):
        """WRITEME
        Calls
          getattr(feature, name)(*args)
        for each feature which has a method called after name.
        """
        for feature in self._features:
            try:
                fn = getattr(feature, name)
            except AttributeError:
                continue

            #####HORRIBLE OPTIONAL ARGUMENT HACK
            try:
                fn(self, *args, **kwargs)
            except TypeError, e:
                if str(e) == "on_change_input() got an unexpected keyword argument 'reason'" and len(kwargs) == 1:
                    fn(self, *args)
                else:
                    raise


    def collect_callbacks(self, name, *args):
        """WRITEME
        Returns a dictionary d such that:
          d[feature] == getattr(feature, name)(*args)
        For each feature which has a method called after name.
        """
        d = {}
        for feature in self._features:
            try:
                fn = getattr(feature, name)
            except AttributeError:
                continue
            d[feature] = fn(*args)
        return d


    ### misc ###

    def toposort(self):
        """WRITEME
        Returns an ordering of the graph's Apply nodes such that:
          - All the nodes of the inputs of a node are before that node.
          - Satisfies the orderings provided by each feature that has
            an 'orderings' method.

        If a feature has an 'orderings' method, it will be called with
        this FunctionGraph as sole argument. It should return a dictionary of
        {node: predecessors} where predecessors is a list of nodes
        that should be computed before the key node.
        """
        if len(self.nodes) < 2:
            # optimization
            # when there are 0 or 1 nodes, no sorting is necessary
            # This special case happens a lot because the OpWiseCLinker produces
            # 1-element graphs.
            return list(self.nodes)
        fg = self
        ords = self.orderings()
        order = graph.io_toposort(fg.inputs, fg.outputs, ords)
        return order

    def orderings(self):
        """
        Return dict d s.t. d[node] is a list of nodes that must be evaluated
        before node itself can be evaluated.

        This is used primarily by the destroy_handler feature to ensure that all
        clients of any destroyed inputs have already computed their outputs.

        :note: This only calls the orderings() fct on all features. It does not
               take care of computing dependencies by itself.

        """
        ords = {}
        for feature in self._features:
            if hasattr(feature, 'orderings'):
                for node, prereqs in feature.orderings(self).items():
                    ords.setdefault(node, []).extend(prereqs)
        # eliminate duplicate prereqs
        for (node,prereqs) in ords.items():
            ords[node] = list(set(prereqs))
        return ords

    def nclients(self, r):
        """WRITEME Same as len(self.clients(r))."""
        return len(self.clients(r))

#     def edge(self, r):
#         return r in self.inputs or r in self.orphans

#     def follow(self, r):
#         node = r.owner
#         if self.edge(r):
#             return None
#         else:
#             if node is None:
#                 raise Exception("what the fuck")
#             return node.inputs

    def check_integrity(self):
        """WRITEME
        Call this for a diagnosis if things go awry.
        """
        nodes = graph.ops(self.inputs, self.outputs)
        if self.nodes != nodes:
            missing = nodes.difference(self.nodes)
            excess = self.nodes.difference(nodes)
            raise Exception("The nodes are inappropriately cached. missing, in excess: ", missing, excess)
        for node in nodes:
            if node.fgraph is not self:
                raise Exception("Node should belong to the FunctionGraph.", node)
            for i, variable in enumerate(node.inputs):
                if variable.fgraph is not self:
                    raise Exception("Input of node should belong to the FunctionGraph.", variable, (node, i))
                if (node, i) not in variable.clients:
                    raise Exception("Inconsistent clients list.", (node, i), variable.clients)
        variables = set(graph.variables(self.inputs, self.outputs))
        if set(self.variables) != variables:
            missing = variables.difference(self.variables)
            excess = self.variables.difference(variables)
            raise Exception("The variables are inappropriately cached. missing, in excess: ", missing, excess)
        for variable in variables:
            if variable.owner is None and variable not in self.inputs and not isinstance(variable, graph.Constant):
                raise Exception("Undeclared input.", variable)
            if variable.fgraph is not self:
                raise Exception("Variable should belong to the FunctionGraph.", variable)
            for node, i in variable.clients:
                if node == 'output':
                    if self.outputs[i] is not variable:
                        raise Exception("Inconsistent clients list.", variable, self.outputs[i])
                    continue
                if node not in nodes:
                    raise Exception("Client not in FunctionGraph.", variable, (node, i))
                if node.inputs[i] is not variable:
                    raise Exception("Inconsistent clients list.", variable, node.inputs[i])

    def __str__(self):
        return "[%s]" % ", ".join(graph.as_string(self.inputs, self.outputs))

    def __repr__(self):
        return self.__str__()


    ### clone ###

    def clone(self):
        """WRITEME"""
        return self.clone_get_equiv()[0]

    def clone_get_equiv(self):
        """WRITEME"""
        equiv = graph.clone_get_equiv(self.inputs, self.outputs)
        self.check_integrity()
        e = FunctionGraph([equiv[i] for i in self.inputs],
                [equiv[o] for o in self.outputs])
        e.check_integrity()
        for feature in self._features:
            e.extend(feature)
        return e, equiv
